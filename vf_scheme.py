#!/usr/bin/env python

import binascii
import hashlib
import logging.config
import math
import os
import random
import struct
import time
from itertools import chain
from argparse import ArgumentParser
from datetime import datetime

# static variables
COMMAND_DELAY = .008    # seconds
TIMESTAMP_LEN = 14      # len(now)
NODE_AMT_LEN = 4
NODE_ID_LEN = 10
SEED_LEN = 10
assert NODE_ID_LEN == SEED_LEN
V_FRAME_FACTOR = 2
V_FRAME_SIZE_LEN = 4
VACK_FRAME_SIZE_LEN = 4
RAND_FRAME_SIZE_LEN = 4
SINGLETON_RATE_THRESHOLD = 0.7
#VFS_DATA_LEN = 50
CALC_COUNT_LIMIT = 20

#log_parser = ArgumentParser()
#log_parser.add_argument('--logfile', dest='log_file', help='log to filename', #default='vfs_multi_nodes.log')
#args, unknown = log_parser.parse_known_args()
#logging.config.fileConfig('logging.ini', defaults={'log_file': args.log_file})
logging.basicConfig(level=logging.DEBUG,
            format='%(name)-12s %(levelname)-8s %(message)s')
logger = logging.getLogger('vf_scheme')
logger.setLevel(logging.INFO)

class VirtualFrameScheme:
    alloc_frame = ['0']
    alloc_frame_last = ['0']
    v_frame = []
    vack_frame = []
    rand_frame = []
    seed = None
    nodes_expect_time = []
    nodes_issue_time = {}
    nodes_data_num = {}
    nodes_data_intime = {}
    nodes_data_number_ok = {}
    # for node, tracking the data upload time and valid vack
    data_issue_time = 0
    data_vack_intime = False
    data_number = 0


    def __init__(self, PacketType, node_slot_time):
        self.vfs_broadcast_id = PacketType.VFS_BROADCAST.index
        self.vfs_send_pkt_id = PacketType.VFS_PKT.index
        self.node_slot_time = node_slot_time
        self.beacon_id = PacketType.BEACON.index
        self.dummy_id = PacketType.DUMMY.index

    def check_broadcast_intime(self,time, last_node_amount):

        if self.data_issue_time+last_node_amount*self.node_slot_time > time: # ok, no timeout
            #self.data_number = self.data_number + 1
            self.data_vack_intime = True
            return True
        else:
            self.data_vack_intime = False
            return False

    def check_node_intime(self, node_id, time, node_amount):
        if node_id in self.nodes_issue_time:
            if self.nodes_issue_time[node_id]+node_amount*self.node_slot_time > time: # ok, no timeout
                self.nodes_data_intime[node_id] = True
                return True
            else:
                return False

    def compute_vf_index(self, v_frame_len, node_id, salt):
        """
        Math hashing: h(Node ID, seed)

        Returns: alloc index
        """
        hashed = hashlib.sha256(node_id + salt).hexdigest()
        hashed_bin = int(hashed, 16)
        vf_index = hashed_bin % v_frame_len

        return vf_index

    def get_random_seed(self):
        """
        Random seeds at size of NODE_ID_LEN. Prefix with leading zeros if chosen number has less length.
        Note: Seed length must be same as Node ID length.

        Returns: seed number
        """
        # seed = '{0:8}'.format(randint(1, 99999999))
        # # replace prefix spaces with 0, if any
        # return seed.replace(' ', '0')
        # return os.urandom(NODE_ID_LEN)

        # Hex seed will double its size, so half the size first
        return binascii.b2a_hex(os.urandom(NODE_ID_LEN/2))

    # TODO: fix rand-frame, time slot for generate vfs
    def generate_seed_v_frame_rand_frame(self, node_id_list):          # BS only
        assert all(len(node_id) == NODE_ID_LEN for node_id in node_id_list),\
            "All Node ID length must be {}".format(NODE_ID_LEN)

        salt = None
        is_below_singleton_rate = True
        calc_count = 0
        while is_below_singleton_rate:
            if calc_count >= CALC_COUNT_LIMIT:
                logger.warning("WARINING: Exceed calculation limit: {}. Stop calculation!".format(calc_count))

            calc_count += 1
            # v-frame length suggested to be at 2 times number of nodes
            self.v_frame = [['0'] for i in range(len(node_id_list) * V_FRAME_FACTOR)]    # 2-D list

            salt = self.get_random_seed()
            for node_id in node_id_list:
                vf_index = self.compute_vf_index(len(self.v_frame), node_id, salt)
                # To detect collision cause, put Node ID in v-frame as identifier
                if self.v_frame[vf_index] == ['0']:
                    self.v_frame[vf_index] = [node_id]
                else:
                    self.v_frame[vf_index].append(node_id)

            # Check if collided node indices is above declared singleton rate
            singleton_nodes_amount = sum(len(l) == 1 and l != ['0'] for l in self.v_frame)
            # Note: Accepting floating number by multipling 1.0
            is_below_singleton_rate = SINGLETON_RATE_THRESHOLD > singleton_nodes_amount / (len(node_id_list) * 1.0)
            logger.debug("singleton_nodes_amount {}/{}, is_below_singleton_rate? {}".format(
                singleton_nodes_amount, len(node_id_list), is_below_singleton_rate))

        raw_v_frame = self.v_frame[:]
        self.alloc_frame_last = list(self.alloc_frame)
        self.alloc_frame = []
        self.rand_frame = []
        for i, node_l in enumerate(self.v_frame):
            if len(node_l) == 1 and node_l != ['0']:    # node in alloc slot
                self.alloc_frame.append(node_l)
                self.v_frame[i] = ['1']
            elif len(node_l) > 1:                # collided slot, put into rand-frame
                self.rand_frame.append(node_l)
                self.v_frame[i] = ['0']
        # self.alloc_frame = filter(lambda l: len(l) == 1 and l != ['0'], self.v_frame)
        # collided_slots = filter(lambda l: len(l) > 1, self.v_frame)
        # Convert nested list to 1-D list
        self.v_frame = list(chain.from_iterable(self.v_frame))
        self.rand_frame = list(chain.from_iterable(self.rand_frame))
        self.alloc_frame = list(chain.from_iterable(self.alloc_frame))
        # # alloc-frame includes nodes from v-frame & rand-frame in order
        # self.alloc_frame += self.rand_frame
        self.seed = salt

        logger.debug("Calculated VFS seed: {}, Singleton rate: {}, calculation count: {}, "
                    "\nraw v-frame {} \nv-frame {} \nrand-frame {} \nalloc-frame {}".format(
                    salt, SINGLETON_RATE_THRESHOLD, calc_count, raw_v_frame, self.v_frame, self.rand_frame,
                    self.alloc_frame))


    def send_dummy_pkt(self, my_tb, pktno=1):     # BS Nodes

        payload_prefix = struct.pack('!H', pktno & 0xffff)
        broadcast = struct.pack('!H', self.dummy_id & 0xffff)

        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)

        payload = payload_prefix + now_timestamp_str + broadcast

        my_tb.txpath.send_pkt(payload)

    def send_beacon_pkt(self,my_tb, pkt_size, pkt_no):        # BS only
        # payload = prefix + now + beacon + dummy

        payload_prefix = struct.pack('!H', pkt_no & 0xffff)
        beacon = struct.pack('!H', self.beacon_id & 0xffff)
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(beacon)
        dummy = (pkt_size - data_size) * chr(pkt_no & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        payload = payload_prefix + now_timestamp_str + beacon + dummy
        my_tb.txpath.send_pkt(payload)
        logger.info("{} broadcast BEACON - {}".format(str(datetime.fromtimestamp(now_timestamp)), pkt_no))

    def broadcast_vfs_pkt(self, my_tb, pkt_size, node_amount, pktno=1):     # BS only
        # payload = prefix + now + vfs_broadcast + node_amount + seed + node_begin_time + len(v-frame) + v-frame + dummy

        # prepare vack frame
        print "alloc_frame_last {}".format(self.alloc_frame_last)
        print "nodes_data_intime {}".format(self.nodes_data_intime)
        self.vack_frame = []
        for i,n_id in enumerate(self.alloc_frame_last):
            if n_id in self.nodes_data_intime and n_id in self.nodes_data_number_ok:
                self.vack_frame.append(str(int(self.nodes_data_intime[n_id] & self.nodes_data_number_ok[n_id] )))
            else:
                self.vack_frame.append('0')
        print "vack_frame {}".format(self.vack_frame)     
        vack_frame_str = ''.join(self.vack_frame) 
        vack_frame_size_str = str(len(vack_frame_str)).zfill(VACK_FRAME_SIZE_LEN)     


        assert self.seed is not None, "Seed is None!"

        v_frame_str = ''.join(self.v_frame)
        # rand_frame_str = ','.join(self.rand_frame)

        payload_prefix = struct.pack('!H', pktno & 0xffff)
        broadcast = struct.pack('!H', self.vfs_broadcast_id & 0xffff)
        node_amount_str = str(node_amount).zfill(NODE_AMT_LEN)
        assert len(node_amount_str) <= NODE_AMT_LEN, "Too much nodes to handle: {}".format(node_amount)
        v_frame_size_str = str(len(v_frame_str)).zfill(V_FRAME_SIZE_LEN)
        assert len(v_frame_size_str) <= V_FRAME_SIZE_LEN, "Too large v-frame size: {}".format(len(v_frame_str))

       

        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(broadcast) + len(node_amount_str) + len(self.seed)\
            + TIMESTAMP_LEN + len(v_frame_size_str) + len(v_frame_str) + len(vack_frame_size_str)  + len(vack_frame_str)
        dummy = (pkt_size - data_size) * chr(pktno & 0xff)

        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        begin_timestamp = math.ceil(now_timestamp + 1)      # begin time buffer 1 second, let all nodes to be ready
        begin_timestamp_str = '{:.3f}'.format(begin_timestamp)
        # For BS recv VFS_PKT session timer use
        self.nodes_expect_time = []
#        for i in range(node_amount):
#            # Each node time slot: 0.5 seconds
#            begin_at = begin_timestamp + (self.node_slot_time * i)
#            end_at = begin_at + 0.5 - 0.0001
#            n_id = self.alloc_frame[i] if i < len(self.alloc_frame) else 'rand_frame'
#            self.nodes_expect_time.append((n_id, begin_at, end_at))



        #node expect time and cmd issue time(the use of timeout)
        self.nodes_issue_time = {}
        self.nodes_data_intime = {}
        #clean up data_number_ok flag
        self.nodes_data_number_ok = {}

        for i in range(node_amount):
            # Each node time slot: self.node_slot_time seconds
            begin_at = now_timestamp + self.node_slot_time * i
            end_at = begin_at + self.node_slot_time
            n_id = self.alloc_frame[i] if i < len(self.alloc_frame) else 'rand_frame'
            self.nodes_expect_time.append((n_id, begin_at, end_at))
            # mark nodes requested time
            if i < len(self.alloc_frame): #leave rand_frame along
                self.nodes_issue_time[n_id] = now_timestamp

            #set timeout flag, init as timout
            if i < len(self.alloc_frame): #leave rand_frame along
                self.nodes_data_intime[n_id] = False            

            #start tracking data number
            if n_id not in self.nodes_data_num: #new node , reset the data number  
                self.nodes_data_num[n_id] = 0

            #start tracking data number ok flag
            if n_id not in self.nodes_data_number_ok: #new node , reset the data number  
                self.nodes_data_number_ok[n_id] = 0

            logger.info("pkt {} node {},{}~{}".format(pktno, n_id, begin_at, end_at))

        payload = payload_prefix + now_timestamp_str + broadcast + node_amount_str + self.seed + begin_timestamp_str\
            + v_frame_size_str + v_frame_str + vack_frame_size_str + vack_frame_str + dummy
        my_tb.txpath.send_pkt(payload)
        #logger.info("{} send VFS_BROADCAST {}, Total nodes: {}, Seed: {}, Node begin: {}, \nSession: {}".format(
        #            str(datetime.fromtimestamp(now_timestamp)), pktno, node_amount, self.seed,
        #            str(datetime.fromtimestamp(begin_timestamp)), self.nodes_expect_time))
        logger.info("{}({}) send VFS_BROADCAST {}, Total nodes: {}, Seed: {},Node begin: {}".format(
                    str(datetime.fromtimestamp(now_timestamp)),now_timestamp, pktno, node_amount, self.seed,
                    str(datetime.fromtimestamp(begin_timestamp)), self.nodes_expect_time))

    def get_node_data_num(self, payload):
        #payload = pktno(2)+TIMESTAMP_LEN+vfs_send_pkt_id(2)+data_size(2)+VFS_DATA_LEN(datasize)+datanum(2)
        prefix_len = 2+TIMESTAMP_LEN+2
        (data_size,) = struct.unpack('!H', payload[prefix_len:prefix_len+2])
        prefix_len = prefix_len + 2 + data_size
        (num_str,) = struct.unpack('!H', payload[prefix_len:prefix_len+2])
        return int(num_str)

    def get_node_data(self, payload):
        #payload = pktno(2)+TIMESTAMP_LEN+vfs_send_pkt_id(2)+data_size(2)+VFS_DATA_LEN(datasize)+datanum(2)
        prefix_len = 2+TIMESTAMP_LEN+2
        (data_size,) = struct.unpack('!H', payload[prefix_len:prefix_len+2])
        #data_size_str = payload[prefix_len:prefix_len+2]
        prefix_len = prefix_len + 2 
        vfs_data = payload[prefix_len:prefix_len+data_size]
        return vfs_data
        #vfs_data = payload[2+TIMESTAMP_LEN+2:2+TIMESTAMP_LEN+2+VFS_DATA_LEN]
        #return vfs_data.lstrip()
   
    def send_vfs_pkt(self, node_id, my_tb, pkt_size, vfs_data, data_num, pktno=1):               # Node only
        # payload = prefix + now + vfs_pkt + data + dummy
        #assert len(vfs_data) <= VFS_DATA_LEN, "wrong vfs_data len {}".format(len(vfs_data))
        #vfs_data = vfs_data.rjust(VFS_DATA_LEN)    # padding with spaces

        payload_prefix = struct.pack('!H', pktno & 0xffff)
        vfs_pkt = struct.pack('!H', self.vfs_send_pkt_id & 0xffff)
        vfs_data_size_str = struct.pack('!H', len(vfs_data) & 0xffff)
        data_num_str = struct.pack('!H', data_num & 0xffff)
        
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(vfs_pkt) +len(vfs_data_size_str) + len(vfs_data) + len(data_num_str)
       
        dummy = (pkt_size - data_size) * chr(pktno & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        payload = payload_prefix + now_timestamp_str + vfs_pkt + vfs_data_size_str + vfs_data + data_num_str + dummy
        my_tb.txpath.send_pkt(payload)

        self.data_issue_time = now_timestamp

        logger.info("{} VFS: Node {} send pktno {} data_num {}".format(str(datetime.fromtimestamp(now_timestamp)), node_id, pktno,data_num))

    def get_node_amount(self, payload):
        node_amount_str = payload[2+TIMESTAMP_LEN+2:
                                  2+TIMESTAMP_LEN+2+NODE_AMT_LEN]
        node_amount = int(node_amount_str)
        return node_amount

    def get_seed(self, payload):
        seed = payload[2+TIMESTAMP_LEN+2+NODE_AMT_LEN:
                       2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN]
        return seed

    def get_begin_time_str(self, payload):
        begin_timestamp_str = payload[2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN:
                                      2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN+TIMESTAMP_LEN]
        return begin_timestamp_str

    def get_v_frame(self, payload):
        prefix_len = 2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN+TIMESTAMP_LEN
        v_frame_size = payload[prefix_len:prefix_len+V_FRAME_SIZE_LEN]
        v_frame_size = int(v_frame_size)
        prefix_len += V_FRAME_SIZE_LEN
        v_frame_str = payload[prefix_len:prefix_len+v_frame_size]

        # prefix_len += v_frame_size
        # rand_frame_size = payload[prefix_len:prefix_len+RAND_FRAME_SIZE_LEN]
        # rand_frame_size = int(rand_frame_size)
        # prefix_len += RAND_FRAME_SIZE_LEN
        # rand_frame_str = payload[prefix_len:prefix_len+rand_frame_size]
        # return list(v_frame_str), rand_frame_str.split(',')

        return list(v_frame_str)

    def get_vack_frame(self, payload):
        prefix_len = 2+TIMESTAMP_LEN+2+NODE_AMT_LEN+SEED_LEN+TIMESTAMP_LEN
        v_frame_size = payload[prefix_len:prefix_len+V_FRAME_SIZE_LEN]
        v_frame_size = int(v_frame_size)
        prefix_len += V_FRAME_SIZE_LEN + v_frame_size
        vack_frame_size = int(payload[prefix_len:prefix_len+VACK_FRAME_SIZE_LEN])
        prefix_len += VACK_FRAME_SIZE_LEN
        vack_frame_str = payload[prefix_len:prefix_len+vack_frame_size]

        return list(vack_frame_str)

    def check_data_num(self,node_id, datanum):
        logger.info("Node {} data num, server {}, node {}".format(node_id,self.nodes_data_num[node_id],datanum))
        if self.nodes_data_num[node_id] == datanum:    
            self.nodes_data_number_ok[node_id] = True  
            return True
        elif self.nodes_data_num[node_id] > datanum:  
            self.nodes_data_number_ok[node_id] = True  
        else:
            logger.error("Node {} data num, server {}, node {}".format(node_id,self.nodes_data_num[node_id],datanum))
            self.nodes_data_number_ok[node_id] = False  
            return False

    def set_data_num(self,node_id, datanum):
        self.nodes_data_num[node_id]  = datanum  
        return True

    def compute_alloc_index(self, vf_index, node_id, v_frame, node_amount):
        if v_frame[vf_index] == '1':    # exists in v-frame
            # alloc-frame = v-frame (reduced '0's)
            v_frame[vf_index] = node_id         # mark position with Node ID
            alloc_frame = [x for x in v_frame if x != '0']
            alloc_index = alloc_frame.index(node_id)
            logger.debug("alloc-index: {}".format(alloc_index))
            return alloc_index, False
        try:                            # fall to rand-frame
            # alloc-frame = v-frame (reduced '0's)
            alloc_frame = [x for x in v_frame if x != '0']
            # randomly choose index after alloc_frame
            rand_index = random.randint(len(alloc_frame), node_amount - 1)
            logger.debug("rand-index: {}".format(rand_index))
            return rand_index, True
        except ValueError:
            logger.error("Node {} has no index!".format(node_id))
            return -1, False

