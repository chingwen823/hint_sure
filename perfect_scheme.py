#!/usr/bin/env python

from gnuradio import digital, gr, blocks, uhd
from gnuradio.eng_option import eng_option
from optparse import OptionParser
from argparse import ArgumentParser
from datetime import datetime
import logging
import logging.config
import os
import threading
import numpy
import hashlib
import binascii
import math

from random import randint
import time, struct, sys

# from current dir
from transmit_path import transmit_path
from receive_path import receive_path
from uhd_interface import uhd_transmitter
from uhd_interface import uhd_receiver


log_parser = ArgumentParser()
log_parser.add_argument('--logfile', dest='log_file', help='log to filename', default='ps_multi_nodes.log')
args, unknown = log_parser.parse_known_args()
logging.config.fileConfig('logging.ini', defaults={'log_file': args.log_file})
logger = logging.getLogger()

# static variables
COMMAND_DELAY = .008    # seconds
NODE_TIME_SLOT = .5     # seconds
TIMESTAMP_LEN = 14      # len(now)
NODE_AMT_LEN = 4
NODE_ID_LEN = 10
SEED_LEN = 10
assert NODE_ID_LEN == SEED_LEN
PS_DATA_LEN = 50


class PerfectScheme:
    alloc_frame = []
    seed = None
    nodes_expect_time = []

    def __init__(self, broadcast_id, send_pkt_id):
        self.ps_broadcast_id = broadcast_id
        self.ps_send_pkt_id = send_pkt_id

    def compute_alloc_index(self, node_amount, node_id, salt):
        """
        Math hashing: h(Node ID, seed)

        Returns: alloc index

        """
        # Python official: hashlib.<crpyto_hash> (openssl_<crypto_hash) is 3x faster than pbkdf2_hmac
        hashed = hashlib.sha256(node_id + salt).hexdigest()
        # derived_key = hashlib.pbkdf2_hmac('sha256', node_id, seed, 10000)
        # hashed = binascii.hexlify(derived_key)
        hashed_bin = int(hashed, 16)
        alloc_index = hashed_bin % node_amount
        return alloc_index

    def get_random_seed(self):
        """
        Random seeds at fixed length 8. Prefix with leading zeros if chosen number has less length.
        Note: Seed length must be same as Node ID length.

        Returns: seed number
        """
        # seed = '{0:8}'.format(randint(1, 99999999))
        # # replace prefix spaces with 0, if any
        # return seed.replace(' ', '0')
        # return os.urandom(NODE_ID_LEN)
        # Hex seed will double its size, so half the size first
        return binascii.b2a_hex(os.urandom(NODE_ID_LEN/2))

    def generate_perfect_seed(self, node_id_list):          # BS only
        assert all(len(node_id) == NODE_ID_LEN for node_id in node_id_list),\
            "All Node ID length must be {}".format(NODE_ID_LEN)

        salt = None
        has_collision = True
        while has_collision:
            self.alloc_frame = ['0'] * len(node_id_list)
            salt = self.get_random_seed()
            for node_id in node_id_list:
                a_index = self.compute_alloc_index(len(node_id_list), node_id, salt)
                self.alloc_frame[a_index] = node_id

            # Check if any calculated node index is collided
            has_collision = any(n == '0' for n in self.alloc_frame)
            # logger.debug("salt {}, alloc {}".format(salt, self.alloc_frame))

        logger.info("Calculated Perfect Seed: {}, Alloc Frame: {}".format(salt, self.alloc_frame))
        self.seed = salt

    def broadcast_ps_pkt(self, my_tb, pkt_size, node_amount, pktno=1):     # BS only
        # payload = prefix + now + ps_broadcast + node_amount + seed + node_begin_time + dummy

        assert self.seed is not None, "Seed is None!"

        payload_prefix = struct.pack('!H', pktno & 0xffff)
        broadcast = struct.pack('!H', self.ps_broadcast_id & 0xffff)
        node_amount_str = str(node_amount).zfill(NODE_AMT_LEN)
        assert len(node_amount_str) <= NODE_AMT_LEN, "Too much nodes to handle: {}".format(node_amount)
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(broadcast) + len(node_amount_str) + len(self.seed)\
            + TIMESTAMP_LEN
        dummy = (pkt_size - data_size) * chr(pktno & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        begin_timestamp = math.ceil(now_timestamp + 1)
        begin_timestamp_str = '{:.3f}'.format(begin_timestamp)
        # For BS recv PS_PKT session timer use
        self.nodes_expect_time = []
        for i in range(node_amount):
            # Each node time slot: 0.5 seconds
            begin_at = begin_timestamp + (NODE_TIME_SLOT * i)
            end_at = begin_at + 0.5 - 0.0001
            self.nodes_expect_time.append((self.alloc_frame[i], begin_at, end_at))
        payload = payload_prefix + now_timestamp_str + broadcast + node_amount_str + self.seed + begin_timestamp_str\
            + dummy
        my_tb.txpath.send_pkt(payload)
        logger.info("{} send PS_BROADCAST {}, Total nodes: {}, Seed: {}, Node begin: {}".format(
            str(datetime.fromtimestamp(now_timestamp)), pktno, node_amount, self.seed,
            str(datetime.fromtimestamp(begin_timestamp))))
        logger.info("Expect time: {}".format(self.nodes_expect_time))

    def send_ps_pkt(self, node_id, my_tb, pkt_size, ps_data, pktno=1):               # Node only
        # payload = prefix + now + ps_pkt + data + dummy

        assert len(ps_data) <= PS_DATA_LEN, "wrong ps_data len {}".format(len(ps_data))
        ps_data = ps_data.rjust(PS_DATA_LEN)    # padding with spaces

        payload_prefix = struct.pack('!H', pktno & 0xffff)
        ps_pkt = struct.pack('!H', self.ps_send_pkt_id & 0xffff)
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(ps_pkt) + PS_DATA_LEN
        dummy = (pkt_size - data_size) * chr(pktno & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        payload = payload_prefix + now_timestamp_str + ps_pkt + ps_data + dummy
        my_tb.txpath.send_pkt(payload)
        logger.info("{} PS: Node {} send data {}".format(str(datetime.fromtimestamp(now_timestamp)), node_id, pktno))

    def get_node_amount(self, payload):
        node_amount_str = payload[2+TIMESTAMP_LEN+2:2+TIMESTAMP_LEN+2+NODE_AMT_LEN]
        node_amount = int(node_amount_str)
        return node_amount

    def get_seed(self, payload):
        seed = payload[2+TIMESTAMP_LEN+2+NODE_AMT_LEN:2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN]
        return seed

    def get_begin_time_str(self, payload):
        begin_timestamp_str = payload[2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN:
                                      2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN+TIMESTAMP_LEN]
        return begin_timestamp_str

    def get_ps_data(self, payload):
        ps_data = payload[2+TIMESTAMP_LEN+2:2+TIMESTAMP_LEN+2+PS_DATA_LEN]
        return ps_data.lstrip()
