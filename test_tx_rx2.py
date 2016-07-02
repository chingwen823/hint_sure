#!/usr/bin/env python

from gnuradio import gr
from gnuradio import eng_notation
from gnuradio.eng_option import eng_option
from optparse import OptionParser
from datetime import datetime
from threading import Thread
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
MAX_COUNT = 10

global stop_init_pkt

stop_init_pkt = False
last_pktno = 0
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

    global n_rcvd, n_right, date_len

    n_rcvd = 0
    n_right = 0
    date_len = 26
    max_pkt = 200
    wrong_pktno = 0xFF00

    # def init_tb():
    #     tb = my_top_block(rx_callback, options)

    def send_data_pkt(my_tb, pktno, pkt_size):
        data = (pkt_size - 2 - date_len) * chr(pktno & 0xff)
        now = str(datetime.now())
        payload = struct.pack('!H', pktno & 0xffff) + now + data
        my_tb.txpath.send_pkt(payload)
        #sys.stderr.write('.')
        print "pktno {}".format(pktno)
        #time.sleep(0.001)

    def send_init_pkt(my_tb, pkt_size):
        pktno = 0
        while pktno < pkt_size:
            if stop_init_pkt:
                break
            data = (pkt_size - 2 - date_len) * chr(pktno & 0xff)
            now = str(datetime.now())
            payload = struct.pack('!H', pktno & 0xffff) + now + data
            my_tb.txpath.send_pkt(payload)
            # sys.stderr.write('.')

            print "init pktno {}".format(pktno)
            pktno += 1
            # time.sleep(0.001)
        time.sleep(2)  # allow time for queued packets to be sent

    def send_ack_pkt(my_tb, temp_list):
        while temp_list:
            pktno, time_data = temp_list.pop(0)
            send_pktno = pktno + 1
            print "Pop pktno {}, time_data {}. Send out pktno {}".format(pktno, time_data, send_pktno)
            data = (pkt_size - 2 - date_len) * chr(pktno & 0xff)
            payload = struct.pack('!H', pktno & 0xffff) + time_data + data
            my_tb.txpath.send_pkt(payload)
            #sys.stderr.write('.')
            #time.sleep(0.001)
        #my_tb.txpath.send_pkt(eof=True)

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
            return

        # print "milk last_pktno {} pktno {}".format(foo.last_pktno, pktno)
        # if pktno <= foo.last_pktno:
        #     print "drop pktno {}".format(pktno)
        #     return

        rxtime = payload[2:date_len+2]
        if ok:
            n_right += 1
        print "received pkt. ok: %r \t pktno: %d \t time: %s \t n_rcvd: %d \t n_right: %d" % (ok, pktno, rxtime, n_rcvd, n_right)

        data_list.append((pktno, rxtime))
        if len(data_list) >= 10:
            stop_init_pkt = True

        # if pktno < max_pkt:
        #     stop_init_pkt = True
        #
        #     # tx - send packet
        #     pktno += 1
        #     data = (pkt_size - 2 - date_len) * chr(pktno & 0xff)
        #     now = str(datetime.now())
        #     payload = struct.pack('!H', pktno & 0xffff) + now + data
        #     tb.txpath.send_pkt(payload)
        #     tb.txpath.send_pkt(eof=True)
        #     #sys.stderr.write('.')
        #     print "sent ack pktno {}".format(pktno)
        #     foo.last_pktno = pktno


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
    #     data = (pkt_size - 2 - date_len) * chr(pktno & 0xff)
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

    send_init_pkt(tb, MAX_PKT_SIZE)

    count = 0
    while count < MAX_COUNT:
        print "count {}; data_list len {}".format(count, len(data_list))
        count += 1
        if data_list:
            #threaded_send_ack_pkt(tb, foo.data_list)
            send_ack_pkt(tb, data_list)
        else:
            # Wait for next round
            time.sleep(2)

    tb.txpath.send_pkt(eof=True)

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
