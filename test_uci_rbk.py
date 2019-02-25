#!/usr/bin/env python

import logging.config
import math
import struct
import sys
import threading
import time
from argparse import ArgumentParser
from datetime import datetime
from gnuradio import digital, gr, uhd
from optparse import OptionParser

import numpy
from enum import Enum
from gnuradio.eng_option import eng_option

from perfect_scheme import PerfectScheme
from vf_scheme import VirtualFrameScheme

from receive_path import receive_path
from transmit_path import transmit_path
from uhd_interface import uhd_receiver
from uhd_interface import uhd_transmitter

# Logging
log_parser = ArgumentParser()
log_parser.add_argument('--logfile', dest='log_file', help='log to filename', default='multi_nodes.log')
args, _ = log_parser.parse_known_args()
logging.config.fileConfig('logging.ini', defaults={'log_file': args.log_file})
logger = logging.getLogger()

# Adjustable variables
REPEAT_TEST_COUNT = 1000
IS_BS_ROLE = True
FRAME_TIME_T = 8        # seconds
assert FRAME_TIME_T >=8, "Shortest adjustable FRAME_TIME_T for VFS is 8 seconds (measured)"

# static variables
COMMAND_DELAY = .008    # seconds
TRANSMIT_DELAY = .1     # seconds
NODE_SLOT_TIME = .5     # seconds

MAX_PKT_AMT = 30
MAX_PKT_AMT_FOR_NODE = 30
MAX_DELTA_AMT = 10
MAX_RTT_AMT = 10
TIMESTAMP_LEN = 14  # 26 # len(now)

# Node: Use device serial number as Node ID
NODE_ID = ''
# BS: Use device serial number as Node ID
# NODE_ID = 'EFR13VEUN'  # N200
# NODE_ID = 'E0R14V7UN'  # N200
# NODE_ID = '3094D5C'     # B210
# NODE_ID = '30757AF'     # N210
# NODE_ID = '3075786'     # N210
NODE_ID_LEN = 10
NODE_ID = NODE_ID.zfill(NODE_ID_LEN)
# BS: presume all known Node IDs
NODE_ID_A, NODE_ID_B = '00030757AF', '0003075786'   # N210
NODE_ID_C = '0003094D5C'    # B210
TEST_NODE_LIST = [NODE_ID_A, NODE_ID_B, NODE_ID_C, '0000000004', '0000000005',
                  '0000000006', '0000000007', '0000000008', '0000000009', '0000000010']

# beacon_random_access = False
my_thread = None
my_iteration = None
nodes_sync_delta = {}
delta_list = []
rtt_list = []
data_list = []

Scheme = Enum(
    'PS',
    'VFS')

PacketType = Enum(
    'NONE',
    'BEACON',
    'RESPOND_BEACON',
    'ACK_RESPOND',
    'PS_BROADCAST',
    'PS_PKT',
    'VFS_BROADCAST',
    'VFS_PKT',
    'CONFIRM_ALLOC',
    'DATA',
    'RBKTESTDOWN',
    'RBKTESTUP')

txrx = Enum(
    'tx',
    'rx')

class my_top_block(gr.top_block):
    def __init__(self, callback, options):
        gr.top_block.__init__(self)

        self.source = uhd_receiver(options.args,
                                   options.bandwidth,
                                   options.rx_freq,
                                   options.lo_offset,
                                   options.rx_gain,
                                   options.spec, 
                                   #options.antenna,
                                   "RX2",
                                   options.clock_source, options.verbose)

        self.sink = uhd_transmitter(options.args,
                                    options.bandwidth, 
                                    options.tx_freq,
                                    options.lo_offset,
                                    #10000000,
                                    options.tx_gain,
                                    options.spec, 
                                    options.antenna,
                                    options.clock_source, options.verbose)


        self.txpath = transmit_path(options)
        self.rxpath = receive_path(callback, options)

        self.connect(self.txpath, self.sink)
        self.connect(self.source, self.rxpath)
        
        """
        Set the center frequency we're interested in.
        """

class RxTopBlock(gr.top_block):
    def __init__(self, callback, options):
        gr.top_block.__init__(self)
        self.source = uhd_receiver(options.args,
                                   options.bandwidth, options.rx_freq,
                                   options.lo_offset, options.rx_gain,
                                   options.spec, options.antenna,
                                   options.clock_source, options.verbose)
        self.rxpath = receive_path(callback, options)
        self.connect(self.source, self.rxpath)


class TxTopBlock(gr.top_block):
    def __init__(self, options):
        gr.top_block.__init__(self)
        self.sink = uhd_transmitter(options.args,
                                    options.bandwidth, options.tx_freq,
                                    options.lo_offset, options.tx_gain,
                                    options.spec, options.antenna,
                                    options.clock_source, options.verbose)
        self.txpath = transmit_path(options)
        self.connect(self.txpath, self.sink)

#RBK test variable
from threading import Event
something_upload_event = Event()


# /////////////////////////////////////////////////////////////////////////////
#                                   main
# /////////////////////////////////////////////////////////////////////////////

def main():

    global n_rcvd, n_right, v_frame, mean_delta, next_tx_ts, stop_rx_ts, ps_end_ts, alloc_index, vf_index,\
        listen_only_to

    n_rcvd = 0
    n_right = 0
    mean_delta = 0
    next_tx_ts = 0
    stop_rx_ts = 0
    ps_end_ts = 0
    alloc_index = -1
    vf_index = -1
    listen_only_to = []
    wrong_pktno = 0xFF00
    v_frame = ''
    vf_len = 8

#rbk test variable
    global down_payload, up_payload, downnum, upnum, tx_tb
    down_payload = struct.pack('!H', 0 & 0xffff)
    up_payload = struct.pack('!H', 0 & 0xffff)
    downnum = 0
    upnum = 0
    
    ps_model = PerfectScheme(PacketType.PS_BROADCAST.index, PacketType.PS_PKT.index, NODE_SLOT_TIME)
    vfs_model = VirtualFrameScheme(PacketType.VFS_BROADCAST.index, PacketType.VFS_PKT.index, NODE_SLOT_TIME)

    def send_beacon_pkt(my_tb, pkt_size, pkt_no):        # BS only
        # payload = prefix + now + beacon + dummy
        payload_header = struct.pack('!H', 0x4141)
        payload_size = struct.pack('!H', 0x0000)
        payload_prefix = struct.pack('!H', pkt_no & 0xffff)
        beacon = struct.pack('!H', PacketType.BEACON.index & 0xffff)
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(beacon)
        dummy = (pkt_size - data_size) * chr(pkt_no & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        payload = payload_header + payload_size + payload_prefix + now_timestamp_str + beacon + dummy
        payload_size = struct.pack('!H', len(payload) & 0xffff)    
        payload = payload_header + payload_size + payload_prefix + now_timestamp_str + beacon + dummy
        my_tb.txpath.send_pkt(payload)
        logger.info("{} broadcast BEACON - {}".format(str(datetime.fromtimestamp(now_timestamp)), pkt_no))

    def send_test_pkt(my_tb, pkt_size, pkt_no, numdata):       
        # payload = prefix + now + beacon + data + dummy
        payload_header = struct.pack('!H', 0x4141)
        payload_size = struct.pack('!H', 0x0000)
        payload_prefix = struct.pack('!H', pkt_no & 0xffff)
        testdown = struct.pack('!H', PacketType.RBKTESTDOWN.index & 0xffff)
        data = struct.pack('!H',numdata & 0xffff)
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(testdown)+ len(data)
        dummy = (pkt_size - data_size) * chr(pkt_no & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        payload = payload_header + payload_size + payload_prefix + now_timestamp_str + testdown + data + dummy
        payload_size = struct.pack('!H', len(payload) & 0xffff)    
        payload = payload_header + payload_size + payload_prefix + now_timestamp_str + testdown + data + dummy
       
        my_tb.txpath.send_pkt(payload)    
#        logger.info(">{:x}\n".format(numdata))

    # Deprecated
    # def send_resp_beacon_pkt(my_tb, pkt_size, pkt_no):     # Node only
    #     # payload = prefix + now + respond_beacon + node_id + dummy
    #
    #     payload_prefix = struct.pack('!H', pkt_no & 0xffff)
    #     respond_beacon = struct.pack('!H', PacketType.RESPOND_BEACON.index & 0xffff)
    #     data_size = len(payload_prefix) + TIMESTAMP_LEN + len(respond_beacon) + NODE_ID_LEN
    #     dummy = (pkt_size - data_size) * chr(pkt_no & 0xff)
    #     now_timestamp = my_tb.sink.get_time_now().get_real_secs()
    #     now_timestamp_str = '{:.3f}'.format(now_timestamp)
    #     payload = payload_prefix + now_timestamp_str + respond_beacon + NODE_ID + dummy
    #     my_tb.txpath.send_pkt(payload)
    #     logger.info("{} send RESPOND_BEACON - {}".format(str(datetime.fromtimestamp(now_timestamp)), pkt_no))
    #
    #     # TODO: how to track rtt?
    #     # Keep rtt_list size limit
    #     rtt_list.append(now_timestamp)
    #     if len(rtt_list) > MAX_RTT_AMT:
    #         rtt_list.pop(0)

    def do_every_beacon(interval, _send_pkt_func, my_tb, pkt_size, max_pkt_amt, iteration=1):
        # For other functions to check these variables
        global my_thread, my_iteration
        my_iteration = iteration

        if iteration < max_pkt_amt:
            # my_thread = threading.Timer(interval, do_every_beacon,
            #                             [interval, _send_pkt_func, my_tb, pkt_amt, 0
            #                                 if iteration == 0 else iteration + 1])
            my_thread = threading.Timer(interval, do_every_beacon,
                                        [interval, _send_pkt_func, my_tb, pkt_size, max_pkt_amt,
                                         max_pkt_amt if iteration >= max_pkt_amt else iteration + 1])
            my_thread.start()
        # execute func
        _send_pkt_func(my_tb, pkt_size, iteration)

    def do_every_protocol_bs(interval, _send_pkt_func, my_tb, pkt_size, node_amt, max_pkt_amt, iteration=1):
        # For other functions to check these variables
        global my_thread, my_iteration
        my_iteration = iteration

        if iteration < max_pkt_amt:
            my_thread = threading.Timer(interval, do_every_protocol_bs,
                                        [interval, _send_pkt_func, my_tb, pkt_size, node_amt, max_pkt_amt,
                                         max_pkt_amt if iteration >= max_pkt_amt else iteration + 1])
            my_thread.start()
        # execute func
        _send_pkt_func(my_tb, pkt_size, node_amt, iteration)

    def do_every_protocol_node(interval, _send_pkt_func, node_id, my_tb, pkt_size, node_data, max_pkt_amt, iteration=1):
        # For other functions to check these variables
        global my_thread, my_iteration
        my_iteration = iteration

        if iteration < max_pkt_amt:
            my_thread = threading.Timer(interval, do_every_protocol_node,
                                        [interval, _send_pkt_func, node_id, my_tb, pkt_size, node_data, max_pkt_amt,
                                         max_pkt_amt if iteration >= max_pkt_amt else iteration + 1])
            my_thread.start()
        # execute func
        _send_pkt_func(node_id, my_tb, pkt_size, node_data, iteration)

#    def rx_rbk_bs_callback(ok, payload):    # For BS
#        global up_payload        
#        up_payload = payload
#        something_upload_event.set()

    def rx_bs_callback(ok, payload):    # For BS
        global n_rcvd, n_right, mean_delta, next_tx_ts, stop_rx_ts, nodes_sync_delta, listen_only_to

        n_rcvd += 1
        
        while(ord(payload[0])!=0xff or ord(payload[1])!=0xff):
            payload = payload[1:]
            if len(payload)<=2:
                return

        (pktno,) = struct.unpack('!H', payload[4:6])
        # Filter out incorrect pkt
        if pktno >= wrong_pktno:
            logger.warning("wrong pktno {}. Drop pkt!".format(pktno))
            return

        try:
            pkt_timestamp_str = payload[6:6+TIMESTAMP_LEN]
            pkt_timestamp = float(pkt_timestamp_str)
        except:
            logger.warning("Timestamp {} is not a float. Drop pkt!".format(pkt_timestamp_str))
            return

        now_timestamp = tx_tb.source.get_time_now().get_real_secs()
#        # now_timestamp_str = '{:.3f}'.format(now_timestamp)
#        delta = now_timestamp - pkt_timestamp   # +ve: Node earlier; -ve: BS earlier
#        if not -5 < delta < 5:
#            logger.warning("Delay out-of-range: {}, timestamp {}. Drop pkt!".format(delta, pkt_timestamp_str))
#            return

        (pkt_type,) = struct.unpack('!H', payload[6+TIMESTAMP_LEN:6+TIMESTAMP_LEN+2])
        # if pkt_type not in [PacketType.RESPOND_BEACON.index, PacketType.PS_PKT.index]:
        if pkt_type not in [PacketType.PS_PKT.index, PacketType.VFS_PKT.index,
                            PacketType.RBKTESTUP.index]:
            logger.warning("Invalid pkt_type {}. Drop pkt!".format(pkt_type))
            return
        if listen_only_to and pkt_type not in listen_only_to:
            str_listen_only_to = [PacketType[x].key for x in listen_only_to]
            logger.warning("Interest only in pkt_type {}, not {}. Drop pkt!".format(
                str_listen_only_to, PacketType[pkt_type].key))
            return

        # Deprecated
        # if pkt_type == PacketType.RESPOND_BEACON.index:
        #     node_id = payload[2+TIMESTAMP_LEN+2:2+TIMESTAMP_LEN+2+NODE_ID_LEN]
        #     if nodes_sync_delta.get(node_id) is None:
        #         nodes_sync_delta[node_id] = []
        #     nodes_sync_delta[node_id].append(delta)
        #     # Keep nodes_sync_delta in size limit
        #     if len(nodes_sync_delta[node_id]) > MAX_DELTA_AMT:
        #         nodes_sync_delta[node_id].pop(0)
        #     mean_delta = numpy.mean(nodes_sync_delta[node_id])
        #
        #     next_tx_ts = now_timestamp + 0.5 - COMMAND_DELAY
        #
        #     logger.info("{} BS recv RESPOND_BEACON from node {}. Node time: {}, Avg delay: {}".format(
        #         str(datetime.fromtimestamp(now_timestamp)), node_id, str(datetime.fromtimestamp(pkt_timestamp)),
        #         mean_delta))
        #     # logger.debug("Node {} len {} {}".format(node_id, len(nodes_sync_delta[node_id]), nodes_sync_delta[node_id]))
        #     # logger.debug("Node {}: {}".format(node_id, nodes_sync_delta[node_id]))
        #     return

        if pkt_type == PacketType.RBKTESTUP.index:
            global upnum
            (num,) = struct.unpack('!H',payload[8+TIMESTAMP_LEN:10+TIMESTAMP_LEN])
#            logger.info"get RBKTESTUP {:x}".format(num))
            upnum = num
#            something_upload_event.set()
            return
            
        if pkt_type == PacketType.PS_PKT.index:
            for i, tpl in enumerate(ps_model.nodes_expect_time):
                node_id, begin_at, end_at = tpl
                if begin_at <= now_timestamp <= end_at:
                    logger.info("{} ({}) [Slot {}: Node {} Session] BS recv PS_PKT {}, data: {}".format(
                        str(datetime.fromtimestamp(now_timestamp)), now_timestamp, i, node_id, pktno,
                        ps_model.get_node_data(payload)))
                    return

            logger.info("{} ({}) [No slot/session] BS recv PS_PKT {}, data: {}".format(
                str(datetime.fromtimestamp(now_timestamp)), now_timestamp, pktno, ps_model.get_node_data(payload)))
            # Last timestamp for PS_PKT session
            #next_tx_ts = ps_model.nodes_expect_time[-1][-1] + 0.2   # add some delay
            return

        if pkt_type == PacketType.VFS_PKT.index:
            for i, tpl in enumerate(vfs_model.nodes_expect_time):
                node_id, begin_at, end_at = tpl
                if begin_at <= now_timestamp <= end_at:
                    logger.info("{} ({}) [Slot {}: Node {} Session] BS recv VFS_PKT {}, data: {}".format(
                        str(datetime.fromtimestamp(now_timestamp)), now_timestamp, i, node_id, pktno,
                        vfs_model.get_node_data(payload)))
                    return

            logger.info("{} ({}) [No slot/session] BS recv VFS_PKT {}, data: {}".format(
                str(datetime.fromtimestamp(now_timestamp)), now_timestamp, pktno, vfs_model.get_node_data(payload)))
            # Last timestamp for VFS_PKT session
            #next_tx_ts = vfs_model.nodes_expect_time[-1][-1] + 0.2   # add some delay
            return

    def rx_node_callback(ok, payload):    # For Node
        global n_rcvd, n_right, mean_delta, next_tx_ts, stop_rx_ts, ps_end_ts, alloc_index, vf_index, \
            listen_only_to

        n_rcvd += 1

        (pktno,) = struct.unpack('!H', payload[0:2])
        # Filter out incorrect pkt
        if pktno >= wrong_pktno:
            logger.warning("Wrong pktno {}. Drop pkt!".format(pktno))
            return

        try:
            pkt_timestamp_str = payload[2:2+TIMESTAMP_LEN]
            pkt_timestamp = float(pkt_timestamp_str)
        except:
            logger.warning("Timestamp {} is not a float. Drop pkt!".format(pkt_timestamp_str))
            return

        now_timestamp = tx_tb.source.get_time_now().get_real_secs()
        # now_timestamp_str = '{:.3f}'.format(now_timestamp)
        delta = now_timestamp - pkt_timestamp   # +ve: BS earlier; -ve: Node earlier
        if not -5 < delta < 5:
            logger.warning("Delay out-of-range: {}, timestamp {}. Drop pkt!".format(delta, pkt_timestamp_str))
            return

        (pkt_type,) = struct.unpack('!H', payload[2+TIMESTAMP_LEN:2+TIMESTAMP_LEN+2])
        if pkt_type not in [PacketType.BEACON.index, PacketType.ACK_RESPOND.index,
                            PacketType.PS_BROADCAST.index, PacketType.VFS_BROADCAST.index]:
            logger.warning("Invalid pkt_type {}. Drop pkt!".format(pkt_type))
            return
        if listen_only_to and pkt_type not in listen_only_to:
            str_listen_only_to = [PacketType[x].key for x in listen_only_to]
            logger.warning("Interest only in pkt_type {}, not {}. Drop pkt!".format(str_listen_only_to, PacketType[pkt_type].key))
            return

        if pkt_type == PacketType.BEACON.index:
            delta_list.append(delta)
            # Keep delta_list in size limit
            if len(delta_list) > MAX_DELTA_AMT:
                delta_list.pop(0)
            mean_delta = numpy.mean(delta_list)
            # mean_delta_str = '{:07.3f}'.format(delta)
            # Adjust time if needed
            if not -0.05 <= mean_delta <= 0.05:
#                rx_tb.source.set_time_now(uhd.time_spec(pkt_timestamp))
#                now_timestamp = rx_tb.source.get_time_now().get_real_secs()
                logger.info("Adjust time... New time: {}".format(str(datetime.fromtimestamp(now_timestamp))))

            stop_rx_ts = now_timestamp + 0.5 - COMMAND_DELAY
            # Hack: for RX2400
            if pktno >= MAX_PKT_AMT - 10:
                stop_rx_ts -= 0.3

            logger.info("{} Node recv BEACON {}. BS time: {}, Avg delay: {}".format(
                str(datetime.fromtimestamp(now_timestamp)), pktno, str(datetime.fromtimestamp(pkt_timestamp)), mean_delta))
            # logger.debug("stop_rx_ts {}".format(str(datetime.fromtimestamp(stop_rx_ts))))
            return

        if pkt_type == PacketType.PS_BROADCAST.index:
            node_amount = ps_model.get_node_amount(payload)
            seed = ps_model.get_seed(payload)
            alloc_index = ps_model.compute_alloc_index(node_amount, NODE_ID, seed)
            try:
                begin_timestamp_str = ps_model.get_begin_time_str(payload)
                begin_timestamp = float(begin_timestamp_str)
            except:
                logger.warning("begin_timestamp {} is not a float. Drop pkt!".format(begin_timestamp_str))
                return

            stop_rx_ts = now_timestamp + 0.4
            # TODO: Duo to various delays, adjust a bit to before firing round up second
            next_tx_ts = begin_timestamp + (NODE_SLOT_TIME * alloc_index) - TRANSMIT_DELAY
            # Each node time slot at NODE_SLOT_TIME seconds
            ps_end_ts = begin_timestamp + (NODE_SLOT_TIME * node_amount)

            logger.info("{} Node recv PS_BROADCAST {}, BS time {}, Total {}, Seed {}, Index {}, Delay {}".format(
                str(datetime.fromtimestamp(now_timestamp)), pktno, str(datetime.fromtimestamp(pkt_timestamp)),
                node_amount, seed, alloc_index, delta))
            # logger.debug("begin {}, stop_rx_ts {}, next_tx_ts {}, ps_end_ts {}".format(
            #     str(datetime.fromtimestamp(begin_timestamp)), str(datetime.fromtimestamp(stop_rx_ts)),
            #     str(datetime.fromtimestamp(next_tx_ts)), str(datetime.fromtimestamp(ps_end_ts))))
            return

        if pkt_type == PacketType.VFS_BROADCAST.index:
            node_amount = vfs_model.get_node_amount(payload)
            seed = ps_model.get_seed(payload)
            try:
                begin_timestamp_str = vfs_model.get_begin_time_str(payload)
                begin_timestamp = float(begin_timestamp_str)
            except:
                logger.warning("begin_timestamp {} is not a float. Drop pkt!".format(begin_timestamp_str))
                return
            try:
                v_frame = vfs_model.get_v_frame(payload)
            except:
                logger.warning("Cannot extract v-frame. Drop pkt!")
                return
            vf_index = vfs_model.compute_vf_index(len(v_frame), NODE_ID, seed)
            alloc_index, in_rand_frame = vfs_model.compute_alloc_index(vf_index, NODE_ID, v_frame, node_amount)

            stop_rx_ts = now_timestamp + 0.4
            # TODO: Duo to various delays, adjust a bit to before firing round up second
            next_tx_ts = begin_timestamp + (NODE_SLOT_TIME * alloc_index) - TRANSMIT_DELAY

            logger.info("{} Node recv VFS_BROADCAST {}, BS time {}, Total {}, Seed {}, Delay {}, "
                        "\nv-frame index: {}, alloc-index: {}, fall to rand-frame: {},"
                        "\nv-frame: {}"
                        .format(str(datetime.fromtimestamp(now_timestamp)), pktno,
                                str(datetime.fromtimestamp(pkt_timestamp)),
                                node_amount, seed, delta, vf_index, alloc_index, in_rand_frame, v_frame))
            # logger.debug("begin {}, stop_rx_ts {}, next_tx_ts {}".format(
            #     str(datetime.fromtimestamp(begin_timestamp)), str(datetime.fromtimestamp(stop_rx_ts)),
            #     str(datetime.fromtimestamp(next_tx_ts))))
            return

    def fire_at_absolute_second():
        for i in range(10000):
            check_time = tx_tb.sink.get_time_now().get_real_secs()
            pivot_time = math.ceil(check_time)
            variance = pivot_time - check_time
            if -0.0002 < variance < 0.0002:
                logger.info("Fire at absolute {}".format(str(datetime.fromtimestamp(check_time))))
                break
            time.sleep(0.0001)

    def usrp_sleep(interval_sec):
        wake_up_timestamp = tx_tb.sink.get_time_now().get_real_secs() + interval_sec
        for i in range(50000):
            now_timestamp = tx_tb.sink.get_time_now().get_real_secs()
            if now_timestamp >= wake_up_timestamp:
                break
            time.sleep(0.0001)

    def fire_at_expected_time(start_time):
        for i in range(50000):
            now_timestamp = tx_tb.sink.get_time_now().get_real_secs()
            if now_timestamp >= start_time:
                logger.info("Fire at {}".format(str(datetime.fromtimestamp(now_timestamp))))
                return
            time.sleep(0.0001)
        logger.warning("ALERT!! not fire at {}".format(str(datetime.fromtimestamp(start_time))))

    def check_thread_is_done(max_pkt_amt):
        for i in range(10000):
            if not my_thread.is_alive() and my_iteration >= max_pkt_amt:
                now_ts = tx_tb.sink.get_time_now().get_real_secs()
                logger.debug("{} - thread done - ".format(str(datetime.fromtimestamp(now_ts))))
                return
            time.sleep(0.0001)
        now_ts = tx_tb.sink.get_time_now().get_real_secs()
        logger.debug("ALERT!! thread timeout at {}".format(str(datetime.fromtimestamp(now_ts))))


    #######################################
    # main
    #######################################

    parser = OptionParser(option_class=eng_option, conflict_handler="resolve")
    expert_grp = parser.add_option_group("Expert")
    parser.add_option("-s", "--size", type="eng_float", default=400,
                      help="set packet size [default=%default]")
    parser.add_option("-M", "--megabytes", type="eng_float", default=1.0,
                      help="set megabytes to transmit [default=%default]")
    parser.add_option("","--discontinuous", action="store_true", default=False,
                      help="enable discontinuous mode")
    parser.add_option("","--from-file", default=None,
                      help="use intput file for packet contents")
    parser.add_option("","--to-file", default=None,
                      help="Output file for modulated samples")
    # Add unused log_file option to prevent 'no such option' error
    parser.add_option("", "--logfile", default=None)
    parser.add_option("", "--scheme", default=None)

    digital.ofdm_mod.add_options(parser, expert_grp)
    digital.ofdm_demod.add_options(parser, expert_grp)
    transmit_path.add_options(parser, expert_grp)
    receive_path.add_options(parser, expert_grp)
    uhd_transmitter.add_options(parser)
    uhd_receiver.add_options(parser)

    (options, args) = parser.parse_args()
    if len(args) != 0:
        logger.error("Parse error: {}\n".format(sys.stderr))
        sys.exit(1)
    logger.info("----------------------------------------------------------")
    logger.info("Input options: \n{}".format(str(options)))
    logger.info("----------------------------------------------------------\n")

    if options.rx_freq is None or options.tx_freq is None:
        logger.error("You must specify -f FREQ or --freq FREQ\n")
        sys.exit(1)
    if options.scheme is None:
        logger.error("You must specify --scheme SCHEME\n")
        sys.exit(1)
    options.scheme = options.scheme.upper()
    if options.scheme not in [str(e) for e in list(Scheme)]:
        logger.error("Not support scheme: {}\n".format(options.scheme))
        sys.exit(1)

    # Decide is BS or Node role
    IS_BS_ROLE = not bool(options.args)
    
    if IS_BS_ROLE:
        logger.info("----------------------------------------------------------")
        logger.info("I am BS")
        logger.info("----------------------------------------------------------\n")       
    else:
        logger.info("----------------------------------------------------------")
        logger.info("I am Node {}".format(options.args))
        logger.info("----------------------------------------------------------\n")          
    # build tx/rx tables
#    tx_tb = TxTopBlock(options)
    tx_tb = my_top_block(rx_bs_callback,options)  
#    if IS_BS_ROLE:
        #rx_tb = RxTopBlock(rx_bs_callback, options)
#        rx_tb = RxTopBlock(rx_bs_callback, options)

#    else:   # Node role
#        rx_tb = RxTopBlock(rx_node_callback, options)

# Use device serial number as Node ID
    NODE_ID = tx_tb.sink.get_usrp_mboard_serial()
        # Append to required length
    NODE_ID = NODE_ID.zfill(NODE_ID_LEN)
    assert len(NODE_ID) == NODE_ID_LEN, "USRP NODE_ID {} len must be {}".format(NODE_ID, NODE_ID_LEN)
    logger.info("\nNODE ID: {}".format(NODE_ID))

    logger.info("\nClock Rate: {} MHz".format(tx_tb.sink.get_clock_rate() / 1000000))

    logger.info("\n####### Test Protocol: {} #######".format(options.scheme))

    if IS_BS_ROLE:
        logger.info("\nPresume known nodes: {}".format(TEST_NODE_LIST))

    # USRP device aligns with PC time (NTP)
    pc_now = time.time()
    tx_tb.sink.set_time_now(uhd.time_spec(pc_now))
    now_ts = tx_tb.sink.get_time_now().get_real_secs()
    logger.info("\n{} Adjust to PC time: {}\n".format(
                str(datetime.fromtimestamp(time.time())), str(datetime.fromtimestamp(now_ts))))
    # now_ts2 = rx_tb.source.get_time_now().get_real_secs()
    # sys_time = uhd.time_spec.get_system_time().get_real_secs()
    # logger.debug("\n{} Time alignment... Device txtime: {}, rxtime: {}, system time: {}\n".format(
    #              str(datetime.fromtimestamp(time.time())), str(datetime.fromtimestamp(get_time)),
    #              str(datetime.fromtimestamp(now_ts2)), str(datetime.fromtimestamp(sys_time))))

    r = gr.enable_realtime_scheduling()
    if r != gr.RT_OK:
        logger.error("Warning: failed to enable realtime scheduling")

    pkt_size = int(options.size)
    tx_tb.start()
#    rx_tb.start()
    
    if IS_BS_ROLE:
        xmode = txrx.tx        
#        rx_tb.lock()
    else:
        xmode = txrx.rx
#        rx_tb.lock()
#        rx_tb.unlock()        
    
    for y in range(REPEAT_TEST_COUNT):

#        logger.info("====== ROUND {} ==========================================".format(y+1))


        #################### SYNC : START #####################

#        if IS_BS_ROLE:
#            ################# BS #################
#            _sync_start = tx_tb.sink.get_time_now().get_real_secs()
#
#            sync_counts = 2
#            for z in range(sync_counts):
#            # Note: TX cannot be lock initially
##                if z == 0 and y == 0:
##                    rx_tb.lock()
##                else:
##                    tx_tb.unlock()
#
#                logger.info("------ Broadcast Beacon ------")
#                _start = tx_tb.sink.get_time_now().get_real_secs()
#                # BS: Send beacon signals. Time precision thread
#                do_every_beacon(0.005, send_beacon_pkt, tx_tb, pkt_size, MAX_PKT_AMT)
#                # Clocking thread
#                check_thread_is_done(MAX_PKT_AMT)
#                _end = tx_tb.source.get_time_now().get_real_secs()
#                logger.info(" - duration {} -".format(_end - _start))
#                logger.info("------ Broadcast Beacon end --------")
#                tx_tb.lock()
#
#                # sleep longer in last loop, finishing sync cycle
#                sleep_sec = 0.5 if z == sync_counts - 1 else 0.2
#                logger.info("Sleep for {} second\n".format(sleep_sec))
#                usrp_sleep(sleep_sec)
#
#            logger.info(" - Sync duration {} -\n".format(tx_tb.source.get_time_now().get_real_secs() - _sync_start))
#
#            # # Deprecated. PROF TSENG: No need response-beacon, might cause collision
#            # rx_tb.unlock()
#            # logger.info("------ Listening ------")
#            # next_tx_ts = 0  # reset next_tx
#            # while next_tx_ts == 0 or next_tx_ts > now_ts:
#            #     time.sleep(0.01)
#            #     now_ts = rx_tb.source.get_time_now().get_real_secs()
#            #     # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(next_tx_ts))))
#            # logger.info("------ Stop listen at {} ------".format(str(datetime.fromtimestamp(now_ts))))
#            # rx_tb.lock()
            ################ BS end ##############


#        else:
            ################ Node ################
#            # Note: TX cannot be lock initially
#            if y != 0:
#                rx_tb.unlock()
#
#            logger.info("------ Listening ------")
#            stop_rx_ts = 0  # reset
#            while stop_rx_ts == 0 or stop_rx_ts > now_ts:
#                time.sleep(0.01)
#                now_ts = rx_tb.source.get_time_now().get_real_secs()
#                # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(stop_rx_ts))))
#            rx_tb.lock()
#            logger.info("------ Stop listen at {} ------".format(str(datetime.fromtimestamp(now_ts))))

            # Deprecated. PROF TSENG: No need response-beacon, might cause collision
            # for z in range(2):
            #
            #     if z != 0:
            #         rx_tb.unlock()
            #     logger.info("------ Listening ------")
            #     next_tx_ts = 0  # reset next_tx
            #     while next_tx_ts == 0 or next_tx_ts > now_ts:
            #         time.sleep(0.01)
            #         now_ts = rx_tb.source.get_time_now().get_real_secs()
            #         # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(next_tx_ts))))
            #     logger.info("------ Stop listen at {} ------".format(str(datetime.fromtimestamp(now_ts))))
            #     rx_tb.lock()
            #
            #     if z != 0:
            #         tx_tb.unlock()
            #     logger.info("------ Send Response Beacon ------")
            #     _start = tx_tb.sink.get_time_now().get_real_secs()
            #     # Node: Send response-beacon signals. Time precision thread
            #     do_every_beacon(0.005, send_resp_beacon_pkt, tx_tb, pkt_size, MAX_PKT_AMT)
            #     # Clocking thread
            #     check_thread_is_done(MAX_PKT_AMT)
            #     _end = rx_tb.source.get_time_now().get_real_secs()
            #     logger.info(" - duration {} -".format(_end - _start))
            #     logger.info("------ Send Response Beacon end ------")
            #     tx_tb.lock()
            ################ Node end ############

        ######################## SYNC : END #########################

      
        if options.scheme == Scheme.VFS.key:
            ################### Virtual Frame Scheme: START ###################
       
        
            if IS_BS_ROLE:

#                if xmode == txrx.rx:
# #                   rx_tb.lock()
#                    xmode=txrx.tx
               
#                tx_tb.source._print_verbage()    
#                tx_tb.sink._print_verbage()  
                
#                logger.info("<{:x}\n".format(upnum))
                send_test_pkt(tx_tb, pkt_size, 0, downnum)
                downnum = upnum + 1


##                rx_tb.unlock()#                tx_tb.stop()                
#                tx_tb.wait()
#                tx_tb.start() 
#                xmode = txrx.rx
#                time.sleep(0.1)
##
#                if False == something_upload_event.wait(0.1):
#                    continue
                
#                something_upload_event.clear()
                    
                ################################                
#                if y<1000:
#                    if y==0:
#                        rx_tb.lock()
#                    send_test_pkt(tx_tb, pkt_size, 0, downnum)
#                    downnum = downnum + 1
#                else:   
#                    if y==1000:
#                        rx_tb.unlock()
#                    if False == something_upload_event.wait(0.1):
#                        continue
#                    something_upload_event.clear()
                ################################
#                if y==0:
#                    rx_tb.lock()
#
#                rx_tb.unlock()  
#                
#                if False == something_upload_event.wait(1):
#                    rx_tb.lock()
#                    continue
#                rx_tb.lock()
#                something_upload_event.clear()
#
#                logger.info("get {}".format(upnum))
#                downnum = upnum + 1
                    
#                #tx_tb.lock()
#                tx_tb.txpath.send_pkt(down_payload)
#                
#                #tx_tb.unlock()
#                
#                (pktno,) = struct.unpack('!H', down_payload[0:2])
#                logger.info("send {}".format(pktno))
#                
#                if y==0:
#                    rx_tb.lock()
#
#                rx_tb.unlock()               
#                
#                count=0
#                while (False==something_upload_event.isSet()):
#                    time.sleep(0.1)
#                    count=count+1
#                    if count>10:
#                    #if False == something_upload_event.wait(1):
#                        #timeout happend
#                        logger.info("timeout\n")                       
#                        break;
#                if count>10:
#                    rx_tb.lock()
#                    continue
#                
#                rx_tb.lock()
#                something_upload_event.clear()
#                (pktno,) = struct.unpack('!H', up_payload[0:2])
#                logger.info("get {}".format(pktno))
#
#                down_payload = struct.pack('!H', (pktno+1) & 0xffff)
                                    
                
               #tx_tb.lock()
               
                ################# BS #################
#                # Deprecated
#                # nodes_sync_delta.update({NODE_ID_A: [1, 2, 3],
#                #                          NODE_ID_B: [4, 5, 6],
#                #                          '0000000003': [7, 8, 9],
#                #                          '0000000004': [10, 11, 12],
#                #                          '0000000005': [13, 14, 15]})
#                # node_amount = len(nodes_sync_delta)
#
#                # Mark Frame T start time & expected end time
#                vfs_start_ts = tx_tb.sink.get_time_now().get_real_secs()
#                vfs_end_ts = vfs_start_ts + FRAME_TIME_T - 0.01     # give a bit deplay for ending
#
#                # calculate VFS seed, v-frame & rand-frame
#                _start = tx_tb.sink.get_time_now().get_real_secs()
#                vfs_model.generate_seed_v_frame_rand_frame(TEST_NODE_LIST)
#                _end = rx_tb.source.get_time_now().get_real_secs()
#                logger.info(" - duration {} -".format(_end - _start))
#
#                tx_tb.unlock()
#                logger.info("------ Broadcast VFS packets ------")
#                # To ensure broadcast end within a full second, adjust to start at absolute second
#                fire_at_absolute_second()
#
#                _start = tx_tb.sink.get_time_now().get_real_secs()
#                do_every_protocol_bs(0.005, vfs_model.broadcast_vfs_pkt, tx_tb, pkt_size, len(TEST_NODE_LIST), MAX_PKT_AMT)
#                # Clocking thread
#                check_thread_is_done(MAX_PKT_AMT)
#                _end = rx_tb.source.get_time_now().get_real_secs()
#                logger.info(" - duration {} -".format(_end - _start))
#                logger.info("------ Broadcast VFS end ------")
#                tx_tb.lock()
#
#                rx_tb.unlock()
#                logger.info("------ Listen VFS packets start ------")
#                listen_only_to = [PacketType.VFS_PKT.index]
#                _start = tx_tb.sink.get_time_now().get_real_secs()
#                # Listen end time is after last node transmission ended, or till frame T ended.
#                stop_rx_ts = vfs_model.nodes_expect_time[-1][-1] + 0.5  # Add misc delay
#                while stop_rx_ts == 0 or stop_rx_ts > now_ts or vfs_end_ts > now_ts:
#                    time.sleep(0.01)
#                    now_ts = rx_tb.source.get_time_now().get_real_secs()
#                    # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(next_tx_ts))))
#                _end = rx_tb.source.get_time_now().get_real_secs()
#                logger.info(" - duration {} -".format(_end - _start))
#                logger.info("------ Listen VFS packets end ------")
#                listen_only_to = []
#                rx_tb.lock()
#
#                now_ts = rx_tb.source.get_time_now().get_real_secs()
#                logger.info("\n - VFS duration {} -".format(now_ts - vfs_start_ts))
#                logger.info("------ VFS cycle ends at {} ------\n".format(str(datetime.fromtimestamp(now_ts))))
#
#                ################# BS end #############

            else:
                ################ Node ################
#                # Mark Frame T start time & expected end time
#                vfs_start_ts = tx_tb.sink.get_time_now().get_real_secs()
#                vfs_end_ts = vfs_start_ts + FRAME_TIME_T - 0.01     # give a bit deplay for ending
#
#                rx_tb.unlock()
#                logger.info("------ Listening VFS broadcast ------")
#                listen_only_to = [PacketType.VFS_BROADCAST.index]
#                stop_rx_ts = 0  # reset
#                while stop_rx_ts == 0 or stop_rx_ts > now_ts:
#                    time.sleep(0.01)
#                    now_ts = rx_tb.source.get_time_now().get_real_secs()
#                    # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(stop_rx_ts))))
#                logger.info("------ Stop listen VFS broadcast at {} ------".format(str(datetime.fromtimestamp(now_ts))))
#                listen_only_to = []
#                rx_tb.lock()
#
#                # TODO: Adjust to node alloc period
#                if alloc_index == -1:
#                    logger.warning("WARNING: alloc_index is -1, cannot join this session. Skip...")
#                    time.sleep(7)
#                    continue
#                # assert alloc_index != -1, "alloc_index is -1"
#
#                logger.info("------ Ready to send VFS packets ------")
#                if y != 0:
#                    tx_tb.unlock()
#
#                fire_at_expected_time(next_tx_ts + COMMAND_DELAY)
#
#                _start = tx_tb.sink.get_time_now().get_real_secs()
#                vfs_data = "Hello, I am node {}".format(NODE_ID)
#                do_every_protocol_node(0.005, vfs_model.send_vfs_pkt, NODE_ID, tx_tb, pkt_size, vfs_data, MAX_PKT_AMT_FOR_NODE)
#                # Clocking thread
#                check_thread_is_done(MAX_PKT_AMT_FOR_NODE)
#                _end = rx_tb.source.get_time_now().get_real_secs()
#                logger.info(" - duration {} -".format(_end - _start))
#                logger.info("------ Send VFS packets end ------")
#                tx_tb.lock()
#
#                # Node wait until VFS cycle is over
#                now_ts = rx_tb.source.get_time_now().get_real_secs()
#                usrp_sleep(vfs_end_ts - now_ts + COMMAND_DELAY)
#
#                now_ts = rx_tb.source.get_time_now().get_real_secs()
#                logger.info("\n - VFS duration {} -".format(now_ts - vfs_start_ts))
                logger.info("------ VFS cycle ends at {} ------\n".format(str(datetime.fromtimestamp(now_ts))))
#                ################ Node end ############

            ##################### Virtual Frame Scheme: END #####################

    #tx_tb.unlock()
    #tx_tb.wait()
    #rx_tb.unlock()
    #rx_tb.wait()
    tx_tb.stop()
#    rx_tb.stop()

    logger.info("\n\n=============================================================================================")
    logger.info("========================================= TEST END ==========================================")
    logger.info("=============================================================================================\n\n")

    sys.exit(0)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        tx_tb.stop()
        tx_tb.wait()
        logger.info("program out")
        pass
