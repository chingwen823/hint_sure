#!/usr/bin/env python

from gnuradio import digital, gr, blocks, uhd
from gnuradio.eng_option import eng_option
from optparse import OptionParser
from argparse import ArgumentParser
from datetime import datetime
from enum import Enum
import logging, logging.config
import threading
import numpy
from random import randint
import time, struct, sys
import binascii
import math

# from this dir
from perfect_scheme import PerfectScheme, NODE_TIME_SLOT
from transmit_path import transmit_path
from receive_path import receive_path
from uhd_interface import uhd_transmitter
from uhd_interface import uhd_receiver


# Logging
log_parser = ArgumentParser()
log_parser.add_argument('--logfile', dest='log_file', help='log to filename', default='ps_multi_nodes.log')
args, unknown = log_parser.parse_known_args()
logging.config.fileConfig('logging.ini', defaults={'log_file': args.log_file})
logger = logging.getLogger()

# static variables
REPEAT_TEST_COUNT = 3
COMMAND_DELAY = .008    # seconds
TRANSMIT_DELAY = .1     # seconds

MAX_PKT_AMT = 30
MAX_PKT_AMT_FOR_NODE = 30
MAX_DELTA_AMT = 10
MAX_RTT_AMT = 10
TIMESTAMP_LEN = 14  # 26 # len(now)

# # Node: Use device serial number as Node ID
NODE_ID = ''
# BS: Use device serial number as Node ID
# NODE_ID = 'EFR13VEUN'  # N200
# NODE_ID = 'E0R14V7UN'  # N200
# NODE_ID = '3094D5C'     # B210
# NODE_ID = '30757AF'     # N210
NODE_ID = '3075786'     # N210
NODE_ID_A, NODE_ID_B = '00030757AF', '0003075786' #N210
NODE_ID_LEN = 10
NODE_ID = NODE_ID.zfill(NODE_ID_LEN)

# beacon_random_access = False
my_thread = None
my_iteration = None
sync_deltas = {}
delta_list = []
rtt_list = []
data_list = []

PacketType = Enum(
    'NONE',
    'BEACON',
    'RESPOND_BEACON',
    'ACK_RESPOND',
    'PS_BROADCAST',
    'PS_PKT',
    'CONFIRM_ALLOC',
    'DATA')


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


# /////////////////////////////////////////////////////////////////////////////
#                                   main
# /////////////////////////////////////////////////////////////////////////////

def main():

    global n_rcvd, n_right, v_frame, mean_delta, next_tx_ts, stop_rx_ts, session_end_ts, alloc_index, node_amount, listen_only_to

    n_rcvd = 0
    n_right = 0
    mean_delta = 0
    next_tx_ts = 0
    stop_rx_ts = 0
    session_end_ts = 0
    alloc_index = -1
    node_amount = 0
    listen_only_to = []
    wrong_pktno = 0xFF00
    v_frame = ''
    vf_len = 8

    ps_model = PerfectScheme(PacketType.PS_BROADCAST.index, PacketType.PS_PKT.index)

    def send_beacon_pkt(my_tb, pkt_size, pkt_no):        # BS only
        # payload = prefix + now + beacon + dummy

        payload_prefix = struct.pack('!H', pkt_no & 0xffff)
        beacon = struct.pack('!H', PacketType.BEACON.index & 0xffff)
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(beacon)
        dummy = (pkt_size - data_size) * chr(pkt_no & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        payload = payload_prefix + now_timestamp_str + beacon + dummy
        my_tb.txpath.send_pkt(payload)
        logger.info("{} broadcast BEACON - {}".format(str(datetime.fromtimestamp(now_timestamp)), pkt_no))

    def send_resp_beacon_pkt(my_tb, pkt_size, pkt_no):     # Node only
        # payload = prefix + now + respond_beacon + node_id + dummy

        payload_prefix = struct.pack('!H', pkt_no & 0xffff)
        respond_beacon = struct.pack('!H', PacketType.RESPOND_BEACON.index & 0xffff)
        data_size = len(payload_prefix) + TIMESTAMP_LEN + len(respond_beacon) + NODE_ID_LEN
        dummy = (pkt_size - data_size) * chr(pkt_no & 0xff)
        now_timestamp = my_tb.sink.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        payload = payload_prefix + now_timestamp_str + respond_beacon + NODE_ID + dummy
        my_tb.txpath.send_pkt(payload)
        logger.info("{} send RESPOND_BEACON - {}".format(str(datetime.fromtimestamp(now_timestamp)), pkt_no))

        # TODO: how to track rtt?
        # Keep rtt_list size limit
        rtt_list.append(now_timestamp)
        if len(rtt_list) > MAX_RTT_AMT:
            rtt_list.pop(0)

    def do_every(interval, _send_pkt_func, my_tb, pkt_size, max_pkt_amt, iteration=1):
        # For other functions to check these variables
        global my_thread, my_iteration
        my_iteration = iteration

        if iteration < max_pkt_amt:
            # my_thread = threading.Timer(interval, do_every,
            #                             [interval, _send_pkt_func, my_tb, pkt_amt, 0
            #                                 if iteration == 0 else iteration + 1])
            my_thread = threading.Timer(interval, do_every,
                                        [interval, _send_pkt_func, my_tb, pkt_size, max_pkt_amt,
                                         max_pkt_amt if iteration >= max_pkt_amt else iteration + 1])
            my_thread.start()
        # execute func
        _send_pkt_func(my_tb, pkt_size, iteration)

    def do_every_ps(interval, _send_pkt_func, my_tb, pkt_size, node_amt, max_pkt_amt, iteration=1):
        # For other functions to check these variables
        global my_thread, my_iteration
        my_iteration = iteration

        if iteration < max_pkt_amt:
            my_thread = threading.Timer(interval, do_every_ps,
                                        [interval, _send_pkt_func, my_tb, pkt_size, node_amt, max_pkt_amt,
                                         max_pkt_amt if iteration >= max_pkt_amt else iteration + 1])
            my_thread.start()
        # execute func
        _send_pkt_func(my_tb, pkt_size, node_amt, iteration)

    def do_every_ps_node(interval, _send_pkt_func, node_id, my_tb, pkt_size, node_data, max_pkt_amt, iteration=1):
        # For other functions to check these variables
        global my_thread, my_iteration
        my_iteration = iteration

        if iteration < max_pkt_amt:
            my_thread = threading.Timer(interval, do_every_ps_node,
                                        [interval, _send_pkt_func, node_id, my_tb, pkt_size, node_data, max_pkt_amt,
                                         max_pkt_amt if iteration >= max_pkt_amt else iteration + 1])
            my_thread.start()
        # execute func
        _send_pkt_func(node_id, my_tb, pkt_size, node_data, iteration)

    def rx_bs_callback(ok, payload):    # For BS
        global n_rcvd, n_right, mean_delta, next_tx_ts, stop_rx_ts, sync_deltas, listen_only_to

        n_rcvd += 1

        (pktno,) = struct.unpack('!H', payload[0:2])
        # Filter out incorrect pkt
        if pktno >= wrong_pktno:
            logger.warning("wrong pktno {}. Drop pkt!".format(pktno))
            return

        try:
            pkt_timestamp_str = payload[2:2+TIMESTAMP_LEN]
            pkt_timestamp = float(pkt_timestamp_str)
        except:
            logger.warning("Timestamp {} is not a float. Drop pkt!".format(pkt_timestamp_str))
            return

        now_timestamp = rx_tb.source.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        delta = now_timestamp - pkt_timestamp   # +ve: Node earlier; -ve: BS earlier
        if not -5 < delta < 5:
            logger.warning("Delay out-of-range: {}, timestamp {}. Drop pkt!".format(delta, pkt_timestamp_str))
            return

        (pkt_type,) = struct.unpack('!H', payload[2+TIMESTAMP_LEN:2+TIMESTAMP_LEN+2])
        if pkt_type not in [PacketType.RESPOND_BEACON.index, PacketType.PS_PKT.index]:
            logger.warning("Invalid pkt_type {}. Drop pkt!".format(pkt_type))
            return
        if listen_only_to and pkt_type not in listen_only_to:
        #if listen_only_to != NONE and listen_only_to != pkt_type:
            str_listen_only_to = [PacketType[x].key for x in listen_only_to]
            logger.warning("Interest only in pkt_type {}, not {}. Drop pkt!".format(str_listen_only_to, PacketType[pkt_type].key))
            return

        if pkt_type == PacketType.RESPOND_BEACON.index:
            node_id = payload[2+TIMESTAMP_LEN+2:2+TIMESTAMP_LEN+2+NODE_ID_LEN]
            if sync_deltas.get(node_id) is None:
                sync_deltas[node_id] = []
            sync_deltas[node_id].append(delta)
            # Keep sync_deltas in size limit
            if len(sync_deltas[node_id]) > MAX_DELTA_AMT:
                sync_deltas[node_id].pop(0)
            mean_delta = numpy.mean(sync_deltas[node_id])

            next_tx_ts = now_timestamp + 0.5 - COMMAND_DELAY

            logger.info("{} BS recv RESPOND_BEACON from node {}. Node time: {}, Avg delay: {}".format(
                str(datetime.fromtimestamp(now_timestamp)), node_id, str(datetime.fromtimestamp(pkt_timestamp)),
                mean_delta))
            # logger.debug("Node {} len {} {}".format(node_id, len(sync_deltas[node_id]), sync_deltas[node_id]))
            # logger.debug("Node {}: {}".format(node_id, sync_deltas[node_id]))
            return

        if pkt_type == PacketType.PS_PKT.index:
            i = 0
            for node_id, begin_at, end_at in ps_model.nodes_expect_time:
                i += 1
                if begin_at <= now_timestamp <= end_at:
                    ps_data = ps_model.get_ps_data(payload)
                    logger.info("{} ({}) [Slot {}: Node {} Session] BS recv PS_PKT {} , data: {}".format(
                        str(datetime.fromtimestamp(now_timestamp)), now_timestamp, i, node_id, pktno, ps_data))
                    return


            logger.info("{} ({}) [No slot/session] BS recv PS_PKT {} , data: {}".format(
                str(datetime.fromtimestamp(now_timestamp)), now_timestamp, pktno, ps_model.get_ps_data(payload)))

            # Last timestamp for PS_PKT session
            #next_tx_ts = ps_model.nodes_expect_time[-1][-1] + 0.2   # add some delay
            return

    def rx_node_callback(ok, payload):    # For Node
        global n_rcvd, n_right, mean_delta, next_tx_ts, stop_rx_ts, session_end_ts, alloc_index, node_amount, listen_only_to

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

        now_timestamp = rx_tb.source.get_time_now().get_real_secs()
        now_timestamp_str = '{:.3f}'.format(now_timestamp)
        delta = now_timestamp - pkt_timestamp   # +ve: BS earlier; -ve: Node earlier
        if not -5 < delta < 5:
            logger.warning("Delay out-of-range: {}, timestamp {}. Drop pkt!".format(delta, pkt_timestamp_str))
            return

        (pkt_type,) = struct.unpack('!H', payload[2+TIMESTAMP_LEN:2+TIMESTAMP_LEN+2])
        if pkt_type not in [PacketType.BEACON.index, PacketType.ACK_RESPOND.index, PacketType.PS_BROADCAST.index]:
            logger.warning("Invalid pkt_type {}. Drop pkt!".format(pkt_type))
            return
        if listen_only_to and pkt_type not in listen_only_to:
        # if listen_only_to != NONE and listen_only_to != pkt_type:
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
                rx_tb.source.set_time_now(uhd.time_spec(pkt_timestamp))
                now_timestamp = rx_tb.source.get_time_now().get_real_secs()
                logger.info("Adjust time... New time: {}".format(str(datetime.fromtimestamp(now_timestamp))))

            stop_rx_ts = now_timestamp + 0.5 - COMMAND_DELAY
            # Hack: for RX2400
            if pktno >= MAX_PKT_AMT - 10:
                stop_rx_ts -= 0.3

            logger.info("{} Node recv BEACON {}. BS time: {}, Avg delay: {}".format(
                str(datetime.fromtimestamp(now_timestamp)), pktno, str(datetime.fromtimestamp(pkt_timestamp)), mean_delta))
            logger.debug("stop_rx_ts {}".format(str(datetime.fromtimestamp(stop_rx_ts))))
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
            next_tx_ts = begin_timestamp + (NODE_TIME_SLOT * alloc_index) - TRANSMIT_DELAY
            # Each node time slot at NODE_TIME_SLOT seconds
            session_end_ts = begin_timestamp + (NODE_TIME_SLOT * node_amount)

            logger.info("{} Node recv PS_BROADCAST {}, BS time {}, Total {}, Seed {}, Index {}, Delay {}".format(
                str(datetime.fromtimestamp(now_timestamp)), pktno, str(datetime.fromtimestamp(pkt_timestamp)),
                node_amount, seed, alloc_index, delta))
            logger.debug("begin {}, stop_rx_ts {}, next_tx_ts {}, session_end_ts {}".format(
                str(datetime.fromtimestamp(begin_timestamp)), str(datetime.fromtimestamp(stop_rx_ts)),
                str(datetime.fromtimestamp(next_tx_ts)), str(datetime.fromtimestamp(session_end_ts))))
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
        for i in range(10000):
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
                logger.debug("{} thread is done".format(str(datetime.fromtimestamp(now_ts))))
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

    ################ Node ################
    if not options.args:
        logger.error("No serial number input!")
        sys.exit(1)

    # build tx/rx tables
    tx_tb = TxTopBlock(options)
    ################ BS ################
    rx_tb = RxTopBlock(rx_bs_callback, options)
    ################ Node ################
    # rx_tb = RxTopBlock(rx_node_callback, options)
    # # Use device serial number as Node ID
    # NODE_ID = tx_tb.sink.get_usrp_mboard_serial()
    # # Append to required length
    # NODE_ID = NODE_ID.zfill(NODE_ID_LEN)
    # assert len(NODE_ID) == NODE_ID_LEN, "USRP NODE_ID {} len must be {}".format(NODE_ID, NODE_ID_LEN)
    # logger.info("\nNODE ID: {}".format(NODE_ID))

    # BS/Node aligned with PC time (NTP)
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
    rx_tb.start()


    for y in range(REPEAT_TEST_COUNT):

        logger.info("\n\n=============================================================================================")
        logger.info("========================================== ROUND {} ==========================================".format(y+1))
        logger.info("=============================================================================================\n\n")


        ##################### SYNC : START #####################

        ################ BS ################

        sync_counts = 2
        for z in range(sync_counts):
        # Note: TX cannot be lock initially
            if z == 0 and y == 0:
                rx_tb.lock()
            else:
                tx_tb.unlock()

            logger.info("------ Broadcast Beacon ------")
            _start = tx_tb.sink.get_time_now().get_real_secs()
            # BS: Send beacon signals. Time precision thread
            do_every(0.005, send_beacon_pkt, tx_tb, pkt_size, MAX_PKT_AMT)
            # Clocking thread
            check_thread_is_done(MAX_PKT_AMT)
            _end = rx_tb.source.get_time_now().get_real_secs()
            logger.info("duration {}".format(_end - _start))
            logger.info("------ Broadcast Beacon end --------")
            tx_tb.lock()

            # sleep longer in last loop, finishing sync cycle
            sleep_sec = 0.5 if z == sync_counts - 1 else 0.2
            logger.info("Sleep for {} second\n".format(sleep_sec))
            usrp_sleep(sleep_sec)

        # # PROF TSENG: No need response-beacon, might cause collision
        #
        # rx_tb.unlock()
        # logger.info("------ Listening ------")
        # next_tx_ts = 0  # reset next_tx
        # while next_tx_ts == 0 or next_tx_ts > now_ts:
        #     time.sleep(0.01)
        #     now_ts = rx_tb.source.get_time_now().get_real_secs()
        #     # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(next_tx_ts))))
        # logger.info("------ Stop listen at {} ------".format(str(datetime.fromtimestamp(now_ts))))
        # rx_tb.lock()

        ################ Node ################

        # # Note: TX cannot be lock initially
        # if y != 0:
        #     rx_tb.unlock()
        #
        # logger.info("------ Listening ------")
        # stop_rx_ts = 0  # reset
        # while stop_rx_ts == 0 or stop_rx_ts > now_ts:
        #     time.sleep(0.01)
        #     now_ts = rx_tb.source.get_time_now().get_real_secs()
        #     # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(stop_rx_ts))))
        # rx_tb.lock()
        # logger.info("------ Stop listen at {} ------".format(str(datetime.fromtimestamp(now_ts))))

        # PROF TSENG: No need response-beacon, might cause collision
        #
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
        #     do_every(0.005, send_resp_beacon_pkt, tx_tb, pkt_size, MAX_PKT_AMT)
        #     # Clocking thread
        #     check_thread_is_done(MAX_PKT_AMT)
        #     _end = rx_tb.source.get_time_now().get_real_secs()
        #     logger.info("duration {}".format(_end - _start))
        #     logger.info("------ Send Response Beacon end ------")
        #     tx_tb.lock()

        ####################### SYNC * 2: END #######################

        ################### Perfect Scheme: START ###################

        ################ BS ################

        # TODO: Add more nodes to test random index
        sync_deltas.update({NODE_ID_A: [1,2,3],
                            NODE_ID_B: [4,5,6],
                            '0000000003': [7,8,9],
                            '0000000004': [10,11,12],
                            '0000000005': [13,14,15]})
        node_amount = len(sync_deltas)

        # rx_tb.lock()

        # PS: calculate perfect seed
        _start = tx_tb.sink.get_time_now().get_real_secs()
        ps_model.generate_perfect_seed(sync_deltas.keys())
        _end = rx_tb.source.get_time_now().get_real_secs()
        logger.info("duration {}".format(_end - _start))

        tx_tb.unlock()
        logger.info("------ Broadcast PS packets ------")
        # To ensure broadcast end within a full second, adjust to start at absolute second
        fire_at_absolute_second()

        _start = tx_tb.sink.get_time_now().get_real_secs()
        # BS: Send ps-broadcast packets.
        do_every_ps(0.005, ps_model.broadcast_ps_pkt, tx_tb, pkt_size, node_amount, MAX_PKT_AMT)
        # Clocking thread
        check_thread_is_done(MAX_PKT_AMT)
        _end = rx_tb.source.get_time_now().get_real_secs()
        logger.info("duration {}".format(_end - _start))
        logger.info("------ Broadcast PS end ------")
        tx_tb.lock()

        rx_tb.unlock()
        logger.info("------ Listen PS packets start ------")
        listen_only_to = [PacketType.PS_PKT.index]
        # Listen end time is after last node transmission ended. Add some misc delay
        stop_rx_ts = ps_model.nodes_expect_time[-1][-1] + 0.2
        while stop_rx_ts == 0 or stop_rx_ts > now_ts:
            time.sleep(0.01)
            now_ts = rx_tb.source.get_time_now().get_real_secs()
            # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(next_tx_ts))))
        logger.info("------ Listen PS packets end ------")
        listen_only_to = []
        rx_tb.lock()

        logger.info("\n------ PS cycle ends at {} ------\n".format(str(datetime.fromtimestamp(now_ts))))

        # logger.info("Sleep for 0.2 second")
        # usrp_sleep(0.2)

        ################ Node ################

        # rx_tb.unlock()
        # logger.info("------ Listening PS broadcast ------")
        # listen_only_to = [PacketType.PS_BROADCAST.index]
        # stop_rx_ts = 0  # reset
        # while stop_rx_ts == 0 or stop_rx_ts > now_ts:
        #     time.sleep(0.01)
        #     now_ts = rx_tb.source.get_time_now().get_real_secs()
        #     # logger.debug("now {} next {}".format(str(datetime.fromtimestamp(now_ts)), str(datetime.fromtimestamp(stop_rx_ts))))
        # logger.info("------ Stop listen PS broadcast at {} ------".format(str(datetime.fromtimestamp(now_ts))))
        # listen_only_to = []
        # rx_tb.lock()
        #
        # # TODO: Adjust to node alloc period
        # assert alloc_index != -1, "alloc_index is {}".format(alloc_index)
        #
        # logger.info("------ Ready to send PS packets ------")
        # if y != 0:
        #     tx_tb.unlock()
        #
        # fire_at_expected_time(next_tx_ts + COMMAND_DELAY)
        #
        # _start = tx_tb.sink.get_time_now().get_real_secs()
        # # BS: Send ps-broadcast packets.
        # ps_data = "Hello, I am node {}".format(NODE_ID)
        # do_every_ps_node(0.005, ps_model.send_ps_pkt, NODE_ID, tx_tb, pkt_size, ps_data, MAX_PKT_AMT_FOR_NODE)
        # # Clocking thread
        # check_thread_is_done(MAX_PKT_AMT_FOR_NODE)
        # _end = rx_tb.source.get_time_now().get_real_secs()
        # logger.info("duration {}".format(_end - _start))
        # logger.info("------ Send PS packets end ------")
        # tx_tb.lock()
        #
        # #fire_at_expected_time(session_end_ts - COMMAND_DELAY)
        # now_ts = rx_tb.source.get_time_now().get_real_secs()
        # usrp_sleep(session_end_ts - now_ts + COMMAND_DELAY)
        # now_ts = rx_tb.source.get_time_now().get_real_secs()
        # logger.info("\n------ PS cycle ends at {} ------\n".format(str(datetime.fromtimestamp(now_ts))))

        ##################### Perfect Scheme: END #####################

    #tx_tb.unlock()
    #tx_tb.wait()
    #rx_tb.unlock()
    #rx_tb.wait()
    tx_tb.stop()
    rx_tb.stop()

    logger.info("\n\n=============================================================================================")
    logger.info("========================================= TEST END ==========================================")
    logger.info("=============================================================================================\n\n")

    sys.exit(0)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
