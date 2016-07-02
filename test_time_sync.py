#!/usr/bin/env python

from gnuradio import gr
from gnuradio import eng_notation
from gnuradio.eng_option import eng_option
from optparse import OptionParser
from datetime import datetime
from threading import Thread
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
MAX_PKT_SIZE = 200
MAX_COUNT = 50

global stop_init_pkt

stop_init_pkt = False
data_list = []

class my_top_block(gr.top_block):
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


# /////////////////////////////////////////////////////////////////////////////
#                                   main
# /////////////////////////////////////////////////////////////////////////////

def main():

    ######## rx part #########

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
        # replace prefix spaces with 0 if any
        return seed.replace(' ', '0')

    # def init_tb():
    #     tb = my_top_block(rx_callback, options)

    # def send_data_pkt(my_tb, pktno, pkt_size):
    #
    #     data_size = 2 + timestamp_len + len(rand_seed) + len(virtual_frame)
    #
    #     dummy_data = (pkt_size - 2 - timestamp_len) * chr(pktno & 0xff)
    #     #now = str(datetime.now())
    #     now_timestamp_str = '{:.3f}'.format(time.time())
    #     payload = struct.pack('!H', pktno & 0xffff) + now_timestamp_str + dummy_data
    #     my_tb.txpath.send_pkt(payload)
    #     #sys.stderr.write('.')
    #     print "pktno {}".format(pktno)
    #     #time.sleep(0.001)

    def send_beacon_pkt(my_tb, pkt_size):
        pktno = 0
        for i in range(pkt_size):
            payload_prefix = struct.pack('!H', pktno & 0xffff)
            data_size = len(payload_prefix) + timestamp_len
            dummy_data = (pkt_size - data_size) * chr(pktno & 0xff)
            #now = str(datetime.now())
            now_timestamp_str = '{:.3f}'.format(time.time())
            payload = payload_prefix + now_timestamp_str + dummy_data
            my_tb.txpath.send_pkt(payload)
            # sys.stderr.write('.')
            print "send beacon signal {}...".format(i)
            time.sleep(0.001)
        time.sleep(0.005)

    def send_init_pkt(my_tb, pkt_size):
        pktno = 1
        while pktno < pkt_size:
            if stop_init_pkt:
                print "init interrupt"
                break

            payload_prefix = struct.pack('!H', pktno & 0xffff)
            rand_seed = get_random_seed()
            data_size = len(payload_prefix) + timestamp_len + len(rand_seed) + len(virtual_frame)
            dummy_data = (pkt_size - data_size) * chr(pktno & 0xff)
            #now = str(datetime.now())
            now_timestamp_str = '{:.3f}'.format(time.time())
            payload = payload_prefix + now_timestamp_str + rand_seed + virtual_frame + dummy_data
            my_tb.txpath.send_pkt(payload)
            # sys.stderr.write('.')
            print "init pktno {}".format(pktno)
            pktno += 1
            time.sleep(0.001)
        # print "sleep 2 seconds"
        time.sleep(0.005)

    def send_ack_pkt(my_tb, temp_list):
        while temp_list:
            pktno, time_data, seed, virtual_frame = temp_list.pop(0)
            ack_pktno = pktno
            payload_prefix = struct.pack('!H', ack_pktno & 0xffff)
            data_size = len(payload_prefix) + timestamp_len + len(seed) + len(virtual_frame)
            dummy_data = (pkt_size - data_size) * chr(ack_pktno & 0xff)
            now_timestamp_str = '{:.3f}'.format(time.time())
            payload = payload_prefix + now_timestamp_str + seed + virtual_frame + dummy_data
            my_tb.txpath.send_pkt(payload)
            #sys.stderr.write('.')
            time_data_str = str(datetime.fromtimestamp(time_data))
            print "Ack pktno {}, time {}, ack_pktno {}, seed {}, vf {}".format(pktno, time_data_str, ack_pktno, seed, virtual_frame)
            time.sleep(0.001)
        #my_tb.txpath.send_pkt(eof=True)
        time.sleep(0.005)

    def threaded_send_ack_pkt(my_tb, temp_list):
        print "threaded_send_ack_pkt"
        thread = Thread(target=send_ack_pkt, args=(my_tb, temp_list))
        thread.start()
        thread.join()

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

        if pktno == 0:
            print "received beacon. time: {}\tdelay: {}".format(rx_time, time_delta)
            return

        if ok:
            n_right += 1
        #print "received pkt. ok: %r \t pktno: %d \t time: %s \t delay: %f \t n_rcvd: %d \t n_right: %d" % (ok, pktno, rx_time, time_delta, n_rcvd, n_right)
        print "received pkt. ok: {}\tpktno: {}\ttime: {}\tdelay: {}\tseed: {}\tvf: {}".format(ok, pktno, rx_time, time_delta, seed, virtual_frame)

        data_list.append((pktno, pkt_timestamp, seed, virtual_frame))
        if len(data_list) >= 10:
            stop_init_pkt = True

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
    print 'milk options {}\n'.format(str(options))

    if options.rx_freq is None or options.tx_freq is None:
        sys.stderr.write("You must specify -f FREQ or --freq FREQ\n")
        parser.print_help(sys.stderr)
        sys.exit(1)

    # build the tx/rx graph
    tb = my_top_block(rx_callback, options)
    #init_tb()
    # tb_rx = rx_top_block(rx_callback, options)
    # tb_tx = tx_top_block(options)

    r = gr.enable_realtime_scheduling()
    if r != gr.RT_OK:
        print "Warning: failed to enable realtime scheduling"

    tb.start()
    # tb_tx.start()
    # tb_rx.start()
    # tb_rx.wait()

    # send init packet data

    # nbytes = int(1e6 * options.megabytes)
    # pktno = 0
    pkt_size = int(options.size)
    # print "milk nbytes {}".format(nbytes)
    # print "milk pkt_size {}".format(pkt_size)

    # while pktno < MAX_PKT_SIZE:
    #     if stop_init_pkt:
    #         break
    #     data = (pkt_size - 2 - timestamp_len) * chr(pktno & 0xff)
    #     now = str(datetime.now())
    #     payload = struct.pack('!H', pktno & 0xffff) + now + data
    #     tb.txpath.send_pkt(payload)
    #     #sys.stderr.write('.')
    #
    #     # TODO: temp add pkt to data_list
    #     #foo.data_list.append((pktno, now))
    #
    #     print "init pktno {}".format(pktno)
    #     pktno += 1
    #     #time.sleep(0.001)
    # time.sleep(2)               # allow time for queued packets to be sent

    # Pre-amble signal
    send_beacon_pkt(tb, 100)
    send_init_pkt(tb, MAX_PKT_SIZE)

    count = 0
    while count < MAX_COUNT:
        print "count {}; data_list len {}".format(count, len(data_list))
        count += 1
        if data_list:
            #threaded_send_ack_pkt(tb, foo.data_list)
            send_ack_pkt(tb, data_list)
        else:
            send_beacon_pkt(tb, 20)
            # Wait for next round
            #time.sleep(2)

    tb.txpath.send_pkt(eof=True)
    time.sleep(5)

    #tb.stop()
    tb.wait()

    # tb_tx.wait()                       # wait for it to finish
    # tb_rx.start()                      # start flow graph
    # tb_rx.wait()


    ######## tx part #########
    #
    # def send_pkt(payload='', eof=False):
    #     return tb.txpath.send_pkt(payload, eof)
    #
    # parser = OptionParser(option_class=eng_option, conflict_handler="resolve")
    # expert_grp = parser.add_option_group("Expert")
    # parser.add_option("-s", "--size", type="eng_float", default=400,
    #                   help="set packet size [default=%default]")
    # parser.add_option("-M", "--megabytes", type="eng_float", default=1.0,
    #                   help="set megabytes to transmit [default=%default]")
    # parser.add_option("","--discontinuous", action="store_true", default=False,
    #                   help="enable discontinuous mode")
    # parser.add_option("","--from-file", default=None,
    #                   help="use intput file for packet contents")
    # parser.add_option("","--to-file", default=None,
    #                   help="Output file for modulated samples")
    #
    # transmit_path.add_options(parser, expert_grp)
    # digital.ofdm_mod.add_options(parser, expert_grp)
    # uhd_transmitter.add_options(parser)
    #
    # (options, args) = parser.parse_args ()
    # print 'milk options {}'.format(str(options))
    #
    # # build the graph
    # tb = my_top_block(options)
    #
    # r = gr.enable_realtime_scheduling()
    # if r != gr.RT_OK:
    #     print "Warning: failed to enable realtime scheduling"
    #
    # tb.start()                       # start flow graph
    #
    # # generate and send packets
    # nbytes = int(1e6 * options.megabytes)
    # n = 0
    # pktno = 0
    # pkt_size = int(options.size)
    # print "milk nbytes {}".format(nbytes)
    # print "milk pkt_size {}".format(pkt_size)
    #
    # while n < nbytes:
    #     if options.from_file is None:
    #         data = (pkt_size - 2) * chr(pktno & 0xff)
    #     else:
    #         data = source_file.read(pkt_size - 2)
    #         if data == '':
    #             break;
    #
    #     payload = struct.pack('!H', pktno & 0xffff) + data
    #
    #     send_pkt(payload)
    #     n += len(payload)
    #     sys.stderr.write('.')
    #     if options.discontinuous and pktno % 5 == 4:
    #         time.sleep(1)
    #     pktno += 1
    #
    # send_pkt(eof=True)
    # time.sleep(2)               # allow time for queued packets to be sent
    # tb.wait()                   # wait for it to finish

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
