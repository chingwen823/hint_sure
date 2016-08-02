#!/usr/bin/env python

from gnuradio import gr
from gnuradio.eng_option import eng_option
from optparse import OptionParser
from datetime import datetime
from threading import Thread
import threading
from random import randint
import time, struct, sys

from gnuradio import digital
from gnuradio import blocks

# from current dir
from transmit_path import transmit_path
from receive_path import receive_path
from uhd_interface import uhd_transmitter
from uhd_interface import uhd_receiver

# static variables
MAX_PKT_AMT = 20
MAX_ITERATION = 50

stop_init_pkt = False
my_thread = None
my_iterations = None
data_list = []


class TopBlock(gr.top_block):
    def __init__(self, callback, options):
        gr.top_block.__init__(self)
        self.source = uhd_receiver(options.args,
                                   options.bandwidth, options.rx_freq,
                                   options.lo_offset, options.rx_gain,
                                   options.spec, options.antenna,
                                   options.clock_source, options.verbose)
        self.sink = uhd_transmitter(options.args,
                                    options.bandwidth, options.tx_freq,
                                    options.lo_offset, options.tx_gain,
                                    options.spec, options.antenna,
                                    options.clock_source, options.verbose)
        self.txpath = transmit_path(options)
        self.rxpath = receive_path(callback, options)
        self.connect(self.source, self.rxpath)
        self.connect(self.txpath, self.sink)


# def time_sync(accepted_threshold):
#     def decorator(func):
#         def func_wrapper(*args, **kwargs):
#             print "-----decorator START: {}".format(accepted_threshold)
#             # TODO: how to get delay? Compare with previous & calc time variance? How to pass to here? How to pass over?
#             func(*args, **kwargs)
#             print "-----decorator END"
#         return func_wrapper
#     return decorator


# /////////////////////////////////////////////////////////////////////////////
#                                   main
# /////////////////////////////////////////////////////////////////////////////

def main():

    global n_rcvd, n_right, timestamp_len, virtual_frame

    n_rcvd = 0
    n_right = 0
    timestamp_len = 14 #26 # len(now)
    max_pkt = 200
    wrong_pktno = 0xFF00
    seed_len = 20
    virtual_frame = '00000000'
    vf_len = 8

    def get_random_seed():
        seed = '{0:20}'.format(randint(1, 99999999999999999999))
        # replace prefix spaces with 0, if any
        return seed.replace(' ', '0')

    def send_beacon_pkt(my_tb, pkt_amount):
        pktno = 0 # 0 as beacon
        for i in range(pkt_amount):
            payload_prefix = struct.pack('!H', pktno & 0xffff)
            data_size = len(payload_prefix) + timestamp_len
            print "data_size {}".format(data_size)
            dummy_data = (pkt_amount - data_size) * chr(pktno & 0xff)
            # now = str(datetime.now())
            now_timestamp_str = '{:.3f}'.format(time.time())
            payload = payload_prefix + now_timestamp_str + dummy_data
            my_tb.txpath.send_pkt(payload)
            # sys.stderr.write('.')
            print "{} send beacon signal {}...".format(str(datetime.now()), i)
            time.sleep(0.001)
        time.sleep(0.005)

    def send_beacon_pkt2(my_tb, pkt_amount, i):
        pktno = 0   # as beacon
        payload_prefix = struct.pack('!H', pktno & 0xffff)
        data_size = len(payload_prefix) + timestamp_len
        dummy_data = (pkt_amount - data_size) * chr(pktno & 0xff)
        now_timestamp_str = '{:.3f}'.format(time.time())
        payload = payload_prefix + now_timestamp_str + dummy_data
        my_tb.txpath.send_pkt(payload)
        # sys.stderr.write('.')
        print "{} send beacon signal {}...".format(str(datetime.now()), i)

    def do_every(interval, send_pkt_func, my_tb, pkt_amt, iterations=1):
        # For other functions to check these variables
        global my_thread, my_iterations
        my_iterations = iterations

        if iterations < pkt_amt:
            # my_thread = threading.Timer(interval, do_every,
            #                             [interval, send_pkt_func, my_tb, pkt_amt, 0
            #                                 if iterations == 0 else iterations + 1])
            my_thread = threading.Timer(interval, do_every,
                                        [interval, send_pkt_func, my_tb, pkt_amt,
                                         pkt_amt if iterations >= pkt_amt else iterations + 1])
            #print "start thread"
            my_thread.start()
        # execute func
        send_pkt_func(my_tb, pkt_amt, iterations)

    # def send_init_pkt(my_tb, pkt_amount):
    #     pktno = 1
    #     while pktno < pkt_amount:
    #         if stop_init_pkt:
    #             print "init interrupted!!!"
    #             break
    #
    #         payload_prefix = struct.pack('!H', pktno & 0xffff)
    #         rand_seed = get_random_seed()
    #         data_size = len(payload_prefix) + timestamp_len + len(rand_seed) + len(virtual_frame)
    #         dummy_data = (pkt_amount - data_size) * chr(pktno & 0xff)
    #         # now = str(datetime.now())
    #         now_timestamp_str = '{:.3f}'.format(time.time())
    #         payload = payload_prefix + now_timestamp_str + rand_seed + virtual_frame + dummy_data
    #         my_tb.txpath.send_pkt(payload)
    #         # sys.stderr.write('.')
    #         print "{} init pktno {}".format(str(datetime.now()), pktno)
    #         pktno += 1
    #         time.sleep(0.001)
    #     # print "sleep 2 seconds"
    #     time.sleep(0.005)

    def send_init_pkt2(my_tb, pkt_amount, pktno=1):
        global stop_init_pkt

        if stop_init_pkt:
            print "init interrupted!!!"
            my_thread.cancel()
            return

        payload_prefix = struct.pack('!H', pktno & 0xffff)
        rand_seed = get_random_seed()
        data_size = len(payload_prefix) + timestamp_len + len(rand_seed) + len(virtual_frame)
        dummy_data = (pkt_amount - data_size) * chr(pktno & 0xff)
        # now = str(datetime.now())
        now_timestamp_str = '{:.3f}'.format(time.time())
        payload = payload_prefix + now_timestamp_str + rand_seed + virtual_frame + dummy_data
        my_tb.txpath.send_pkt(payload)
        # sys.stderr.write('.')
        print "{} init pktno {}".format(str(datetime.now()), pktno)

    # @time_sync(10)
    # def send_ack_pkt(my_tb, temp_list):
    #     while temp_list:
    #         pktno, time_data, seed, virtual_frame = temp_list.pop(0)
    #         ack_pktno = pktno
    #         payload_prefix = struct.pack('!H', ack_pktno & 0xffff)
    #         data_size = len(payload_prefix) + timestamp_len + len(seed) + len(virtual_frame)
    #         dummy_data = (pkt_amt - data_size) * chr(ack_pktno & 0xff)
    #         now_timestamp_str = '{:.3f}'.format(time.time())
    #         payload = payload_prefix + now_timestamp_str + seed + virtual_frame + dummy_data
    #         my_tb.txpath.send_pkt(payload)
    #         #sys.stderr.write('.')
    #         time_data_str = str(datetime.fromtimestamp(time_data))
    #         print "Ack pktno {}, time {}, ack_pktno {}, seed {}, vf {}".format(pktno, time_data_str, ack_pktno, seed, virtual_frame)
    #         time.sleep(0.001)
    #     #my_tb.txpath.send_pkt(eof=True)
    #     time.sleep(0.005)

    def send_ack_pkt2(my_tb, temp_list):
        while temp_list:
            pktno, time_data, seed, virtual_frame = temp_list.pop(0)
            ack_pktno = pktno
            payload_prefix = struct.pack('!H', ack_pktno & 0xffff)
            data_size = len(payload_prefix) + timestamp_len + len(seed) + len(virtual_frame)
            dummy_data = (pkt_amt - data_size) * chr(ack_pktno & 0xff)
            now_timestamp_str = '{:.3f}'.format(time.time())
            payload = payload_prefix + now_timestamp_str + seed + virtual_frame + dummy_data
            my_tb.txpath.send_pkt(payload)
            #sys.stderr.write('.')
            time_data_str = str(datetime.fromtimestamp(time_data))
            print "Ack pktno {}, time {}, ack_pktno {}, seed {}, vf {}".format(pktno, time_data_str, ack_pktno, seed, virtual_frame)
            time.sleep(0.001)
        #my_tb.txpath.send_pkt(eof=True)
        time.sleep(0.005)

    def rx_callback(ok, payload):
        global n_rcvd, n_right, stop_init_pkt

        n_rcvd += 1
        (pktno,) = struct.unpack('!H', payload[0:2])

        # Filter out incorrect pkt
        if pktno >= wrong_pktno:
            print "wrong pktno {}".format(pktno)
            return

        try:
            pkt_timestamp_str = payload[2:2+timestamp_len]
            pkt_timestamp = float(pkt_timestamp_str)
        except:
            print "{} is not a float.".format(pkt_timestamp_str)
            return

        seed = payload[2+timestamp_len:2+timestamp_len+seed_len]
        virtual_frame = payload[2+timestamp_len+seed_len:2+timestamp_len+seed_len+vf_len]

        now_timestamp = round(time.time(), 3)
        time_delta = now_timestamp - pkt_timestamp
        rx_time = str(datetime.fromtimestamp(pkt_timestamp))

        if pktno == 0:  # is beacon
            print "received beacon. time: {}\tdelay: {}".format(rx_time, time_delta)
            return

        if ok:
            n_right += 1
        #print "received pkt. ok: %r \t pktno: %d \t time: %s \t delay: %f \t n_rcvd: %d \t n_right: %d" % (ok, pktno, rx_time, time_delta, n_rcvd, n_right)
        print "received pkt. ok: {}\tpktno: {}\ttime: {}\tdelay: {}\tseed: {}\tvf: {}".format(ok, pktno, rx_time, time_delta, seed, virtual_frame)

        data_list.append((pktno, pkt_timestamp, seed, virtual_frame))
        if len(data_list) >= 10:
            stop_init_pkt = True

    def check_thread_is_done(iter_limit):
        for i in range(1000):
            if not my_thread.is_alive() and my_iterations >= iter_limit:
                # thread done, proceed to next
                break
            time.sleep(0.002)
        print "{} thread is done".format(str(datetime.now()))

    #######
    # main
    #######

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

    digital.ofdm_mod.add_options(parser, expert_grp)
    digital.ofdm_demod.add_options(parser, expert_grp)
    transmit_path.add_options(parser, expert_grp)
    receive_path.add_options(parser, expert_grp)
    uhd_transmitter.add_options(parser)
    uhd_receiver.add_options(parser)

    (options, args) = parser.parse_args ()
    if len(args) != 0:
        parser.print_help(sys.stderr)
        sys.exit(1)
    print "----------------------------------------------------------"
    print "Input options: \n{}".format(str(options))
    print "----------------------------------------------------------\n"

    if options.rx_freq is None or options.tx_freq is None:
        sys.stderr.write("You must specify -f FREQ or --freq FREQ\n")
        parser.print_help(sys.stderr)
        sys.exit(1)

    # build the tx/rx graph
    tb = TopBlock(rx_callback, options)
    #init_tb()
    # tb_rx = rx_top_block(rx_callback, options)
    # tb_tx = tx_top_block(options)

    r = gr.enable_realtime_scheduling()
    if r != gr.RT_OK:
        print "Warning: failed to enable realtime scheduling"

    tb.start()

    pkt_amt = int(options.size)
    print "pkt_amt {}".format(pkt_amt)

    pkt_amount = 100

    # Send beacon signals. Time precision: New thread
    do_every(0.005, send_beacon_pkt2, tb, pkt_amount)
    # New thread checks last thread is done
    check_thread_is_done(pkt_amount)
    # Send initial data packets. Time precision: New thread
    do_every(0.005, send_init_pkt2, tb, pkt_amount)
    # New thread checks last thread is done
    check_thread_is_done(pkt_amount)


    # send_init_pkt(tb, MAX_PKT_AMT)

    # Iteration for switching between beacon signals & data packets
    # iterate = 0
    # while iterate < MAX_ITERATION:
    #     print "iterate {}: data_list len {}".format(iterate, len(data_list))
    #     iterate += 1
    #     if data_list:
    #         send_ack_pkt2(tb, data_list)
    #         # New thread checks last thread is done
    #         check_thread_is_done(50)
    #     else:
    #         do_every(0.005, send_beacon_pkt2, tb, 50)
    #         # Another thread to check last thread is done
    #         check_thread_is_done(50)

    # TODO: Estimate when send EOF will finish... But does it needed?
    # time.sleep(5)
    # print "TX send EOF pkt.............."
    # tb.txpath.send_pkt(eof=True)

    #tb.stop()
    tb.wait()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
