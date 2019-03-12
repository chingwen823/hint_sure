#!/usr/bin/env python
#
# Copyright 2006,2007,2011,2013 Free Software Foundation, Inc.
# 
# This file is part of GNU Radio
# 
# GNU Radio is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# 
# GNU Radio is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with GNU Radio; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
# 


from gnuradio import gr
from gnuradio import eng_notation
from gnuradio.eng_option import eng_option
from optparse import OptionParser

from gnuradio import blocks
from gnuradio import digital
import time, struct, sys


# from current dir
from transmit_path import transmit_path
from uhd_interface import uhd_transmitter
from receive_path import receive_path
from uhd_interface import uhd_receiver

import struct, sys

######################################
#  Hint protocol import and defines 
######################################
import numpy
from enum import Enum 

import logging.config

# protocol
from vf_scheme import VirtualFrameScheme

#presum
NODE_SLOT_TIME = .5     # seconds
TIMESTAMP_LEN = 14  # 26 # len(now)

# BS: presume all known Node IDs
NODE_ID_A, NODE_ID_B = '00030757AF', '0003075786'   # N210
NODE_ID_C = '0003094D5C'    # B210
TEST_NODE_LIST = [NODE_ID_A, NODE_ID_B, NODE_ID_C, '0000000004', '0000000005',
                  '0000000006', '0000000007', '0000000008', '0000000009', '0000000010']
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
    'DATA')

#logger config
#logging.config.fileConfig('logging.ini', defaults={'log_file': args.log_file})
logger = logging.getLogger()

class my_top_block(gr.top_block):
    def __init__(self, callback, options):
        gr.top_block.__init__(self)

        if(options.tx_freq is not None): 
	    self.sink = uhd_transmitter(options.args,
                                       options.bandwidth, options.tx_freq, 
                                       options.lo_offset, options.tx_gain,
                                       options.spec, options.antenna,
                                       options.clock_source, options.verbose)
	elif(options.to_file is not None):
	    self.sink = blocks.file_sink(gr.sizeof_gr_complex, options.to_file)
	else:
	    self.sink = blocks.null_sink(gr.sizeof_gr_complex)

        if(options.rx_freq is not None):
            self.source = uhd_receiver(options.args,
                                       options.bandwidth, options.rx_freq, 
                                       options.lo_offset, options.rx_gain,
                                       options.spec, options.antenna,
                                       options.clock_source, options.verbose)
        elif(options.from_file is not None):
            self.source = blocks.file_source(gr.sizeof_gr_complex, options.from_file)
        else:
            self.source = blocks.null_source(gr.sizeof_gr_complex)


        # Set up receive path
        # do this after for any adjustments to the options that may
        # occur in the sinks (specifically the UHD sink)
        self.rxpath = receive_path(callback, options)
	self.txpath = transmit_path(options)

        self.connect(self.source, self.rxpath)
	self.connect(self.txpath, self.sink)
        

# /////////////////////////////////////////////////////////////////////////////
#                                   main
# /////////////////////////////////////////////////////////////////////////////

def main():
    
    #import protocol model
    vfs_model = VirtualFrameScheme(PacketType.VFS_BROADCAST.index, PacketType.VFS_PKT.index, NODE_SLOT_TIME)


    def send_pkt(payload='', eof=False):
        return tb.txpath.send_pkt(payload, eof)

    global n_rcvd, n_right
        
    n_rcvd = 0
    n_right = 0

    def rx_callback(ok, payload):
        global n_rcvd, n_right
        n_rcvd += 1
	
        # Filter out incorrect pkt
        if not ok:
            logger.warning("Packet fail. Drop pkt!")
            return
	
        n_right += 1

        (pktno,) = struct.unpack('!H', payload[0:2])


        (pkt_type,) = struct.unpack('!H', payload[2+TIMESTAMP_LEN:2+TIMESTAMP_LEN+2])
        if pkt_type not in [PacketType.BEACON.index, PacketType.ACK_RESPOND.index,
                            PacketType.PS_BROADCAST.index, PacketType.VFS_BROADCAST.index]:
            logger.warning("Invalid pkt_type {}. Drop pkt!".format(pkt_type))
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

            logger.critical("{} Node recv VFS_BROADCAST {}, BS time {}, Total {}, Seed {}, Delay {}, "
                        "\nv-frame index: {}, alloc-index: {}, fall to rand-frame: {},"
                        "\nv-frame: {}"
                        .format(str(datetime.fromtimestamp(now_timestamp)), pktno,
                                str(datetime.fromtimestamp(pkt_timestamp)),
                                node_amount, seed, delta, vf_index, alloc_index, in_rand_frame, v_frame))
            # logger.debug("begin {}, stop_rx_ts {}, next_tx_ts {}".format(
            #     str(datetime.fromtimestamp(begin_timestamp)), str(datetime.fromtimestamp(stop_rx_ts)),
            #     str(datetime.fromtimestamp(next_tx_ts))))
            return
    parser = OptionParser(option_class=eng_option, conflict_handler="resolve")
    expert_grp = parser.add_option_group("Expert")
    parser.add_option("","--discontinuous", action="store_true", default=False,
                      help="enable discontinuous")
    parser.add_option("","--from-file", default=None,
                      help="input file of samples to demod")
    parser.add_option("-M", "--megabytes", type="eng_float", default=1.0,
                      help="set megabytes to transmit [default=%default]")
    parser.add_option("-s", "--size", type="eng_float", default=400,
                      help="set packet size [default=%default]")
    parser.add_option("-p", "--packno", type="eng_float", default=0,
                      help="set packet number [default=%default]")

    transmit_path.add_options(parser, expert_grp)
    digital.ofdm_mod.add_options(parser, expert_grp)
    uhd_transmitter.add_options(parser)

    receive_path.add_options(parser, expert_grp)
    uhd_receiver.add_options(parser)
    digital.ofdm_demod.add_options(parser, expert_grp)

    (options, args) = parser.parse_args ()

    # Decide is BS or Node role
    IS_BS_ROLE = options.args
    logger.critical("I am BS")

    if options.from_file is None:
        if options.rx_freq is None:
            sys.stderr.write("You must specify -f FREQ or --freq FREQ\n")
            parser.print_help(sys.stderr)
            sys.exit(1)
    if options.packno is not None:
	packno_delta = options.packno
	print "assign pktno start: %d" % packno_delta

    # build the graph
    tb = my_top_block(rx_callback, options)

    r = gr.enable_realtime_scheduling()
    if r != gr.RT_OK:
        print "Warning: failed to enable realtime scheduling"

    tb.start()                      # start flow graph
    # generate and send packets
    nbytes = int(1e6 * options.megabytes)
    n = 0
    pktno = 0
    pkt_size = int(options.size)

    if IS_BS_ROLE:
	    while n < nbytes:
		if options.from_file is None:
		    data = (pkt_size - 2) * chr(pktno & 0xff) 
		else:
		    data = source_file.read(pkt_size - 2)
		    if data == '':
		        break;
		#prepare 
		vfs_model.generate_seed_v_frame_rand_frame(TEST_NODE_LIST)
		#send boardcast
		vfs_model.broadcast_vfs_pkt(tb, pkt_size, len(TEST_NODE_LIST),pktno+int(packno_delta))
		        
		#payload = struct.pack('!H', (pktno+int(packno_delta)) & 0xffff) + data
		#send_pkt(payload)
		#n += len(payload)
		sys.stderr.write('.')
		if options.discontinuous and pktno % 5 == 4:
		    time.sleep(1)
		pktno += 1
        
    send_pkt(eof=True)
    time.sleep(2)               # allow time for queued packets to be sent
    tb.wait()                       # wait for it to finish

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
