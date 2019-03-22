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
from gnuradio import uhd
import logging.config
from datetime import datetime
import threading
import Queue
from argparse import ArgumentParser
# protocol
from vf_scheme import VirtualFrameScheme

#presum
NODE_RX_MAX = 10
NODE_SLOT_TIME = .5     # seconds
TRANSMIT_DELAY = .1     # seconds
TIMESTAMP_LEN = 14  # 26 # len(now)
MAX_DELTA_AMT = 10
delta_list = []
# Node: Use device serial number as Node ID
NODE_ID = ''
# BS: Use device serial number as Node ID
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
    'DUMMY')

logging.basicConfig(level=logging.INFO,
            format='%(name)-12s %(levelname)-8s %(message)s')
logger = logging.getLogger('hintvfs')
logger.setLevel(logging.INFO)



class my_top_block(gr.top_block):
    def __init__(self, callback, options):
        gr.top_block.__init__(self)

        if(options.tx_freq is not None): 
            self.sink = uhd_transmitter(options.args,
                                       options.bandwidth, options.tx_freq, 
                                       options.lo_offset, options.tx_gain,
                                       options.spec, options.antenna,
                                       options.clock_source, options.verbose)
#        elif(options.to_file is not None):
#            self.sink = blocks.file_sink(gr.sizeof_gr_complex, options.to_file)
        else:
            self.sink = blocks.null_sink(gr.sizeof_gr_complex)

        if(options.rx_freq is not None):
            self.source = uhd_receiver(options.args,
                                       options.bandwidth, options.rx_freq, 
                                       options.lo_offset, options.rx_gain,
                                       options.spec, options.antenna,
                                       options.clock_source, options.verbose)
#        elif(options.from_file is not None):
#            self.source = blocks.file_source(gr.sizeof_gr_complex, options.from_file)
        else:
            self.source = blocks.null_source(gr.sizeof_gr_complex)


        # Set up receive path
        # do this after for any adjustments to the options that may
        # occur in the sinks (specifically the UHD sink)
        self.rxpath = receive_path(callback, options)
        self.txpath = transmit_path(options)

        self.connect(self.source, self.rxpath)
        self.connect(self.txpath, self.sink)
        
def decode_common_pkt_header(tb,payload):
    (pktno,) = struct.unpack('!H', payload[0:2])

    try:
        pkt_timestamp_str = payload[2:2+TIMESTAMP_LEN]
        pkt_timestamp = float(pkt_timestamp_str)
    except:
        logger.warning("Timestamp {} is not a float. Drop pkt!".format(pkt_timestamp_str))
        return 

    now_timestamp = tb.source.get_time_now().get_real_secs()
    # now_timestamp_str = '{:.3f}'.format(now_timestamp)
    delta = now_timestamp - pkt_timestamp   # +ve: BS earlier; -ve: Node earlier
    if not -5 < delta < 5:
        logger.warning("Delay out-of-range: {}, timestamp {}. Drop pkt!".format(delta, pkt_timestamp_str))
        return 

    (pkt_type,) = struct.unpack('!H', payload[2+TIMESTAMP_LEN:2+TIMESTAMP_LEN+2])

    if pkt_type == PacketType.DUMMY.index:
        return

    if pkt_type not in [PacketType.VFS_BROADCAST.index, PacketType.VFS_PKT.index, PacketType.BEACON.index]:
        logger.warning("Invalid pkt_type {}. Drop pkt!".format(pkt_type))
        return 

    return(pktno,pkt_timestamp,pkt_type)

def action(tb, vfs_model, payload,NODE_ID):

    global alloc_index, last_node_amount, file_output, go_on_flag, data_num

    thingy = decode_common_pkt_header(tb,payload)

    if not thingy:
        logger.wran("decode_common_pkt_header return nil")
        return 
    
    (pktno,pkt_timestamp,pkt_type) = thingy
    logger.info("decode_common_pkt_header pktno {}, pkt_ts {}, pkt_type".format(pktno,pkt_timestamp,pkt_type))

    now_timestamp = tb.source.get_time_now().get_real_secs()
    delta = now_timestamp - pkt_timestamp

    if pkt_type == PacketType.BEACON.index:
            delta_list.append(delta)
            # Keep delta_list in size limit
            if len(delta_list) > MAX_DELTA_AMT:
                delta_list.pop(0)
            mean_delta = numpy.mean(delta_list)
            # mean_delta_str = '{:07.3f}'.format(delta)
            # Adjust time if needed
            if not -0.05 <= mean_delta <= 0.05:
                tb.source.set_time_now(uhd.time_spec(pkt_timestamp))
                now_timestamp = tb.source.get_time_now().get_real_secs()
                logger.info("Adjust time... New time: {}".format(str(datetime.fromtimestamp(now_timestamp))))

            logger.info("{} Node recv BEACON {}. BS time: {}, Avg delay: {}".format(
                str(datetime.fromtimestamp(now_timestamp)), pktno, str(datetime.fromtimestamp(pkt_timestamp)), mean_delta))
            # logger.debug("stop_rx_ts {}".format(str(datetime.fromtimestamp(stop_rx_ts))))
            return

    # BS receive from node
    if pkt_type == PacketType.VFS_PKT.index:

        logger.info("identify node from nowtime {}, delta {}".format(now_timestamp,delta))

        for i, tpl in enumerate(vfs_model.nodes_expect_time):
            node_id, begin_at, end_at = tpl

            if begin_at <= now_timestamp <= end_at:
                #check if time out(response in 1 frame time) 
                if vfs_model.check_node_intime( node_id, now_timestamp, len(TEST_NODE_LIST)):
                    logger.info("{} ({}) [Slot {}: Node {} ] BS recv VFS_PKT.index {}, data: {}".format(
                        str(datetime.fromtimestamp(now_timestamp)), now_timestamp, i, node_id, pktno,
                    vfs_model.get_node_data(payload)))
                    data_number = vfs_model.get_node_data_num(payload)
                    return (delta, node_id, pktno, vfs_model.get_node_data(payload), data_number)

                else:
                    logger.info("[Node {} pktno{}] Upload timeout".format(node_id, pktno))
                    return 
                
        logger.info("{} ({}) [No slot/session] BS recv VFS_PKT {}, data: {}".format(
        str(datetime.fromtimestamp(now_timestamp)), now_timestamp, pktno, vfs_model.get_node_data(payload)))
        
        return 

    if pkt_type == PacketType.VFS_BROADCAST.index:


        #check if vack intime(response in 1 frame time) 
        if last_node_amount == -1 or \
            vfs_model.check_broadcast_intime(now_timestamp, (last_node_amount+1)): # give 1 more slot time 
            intime_flag = True
            logger.info("[Node {} pktno{}] VACK intime".format(NODE_ID, pktno))
        else:
            intime_flag = False
            logger.info("[Node {} pktno{}] VACK timeout".format(NODE_ID, pktno))
            
        #if intime, then we can check VACK valid 
        if intime_flag: 
            try:
                vack_frame = vfs_model.get_vack_frame(payload)
            except:
                logger.warning("Cannot extract vack-frame. Drop pkt!")
                go_on_flag = False
                return 

            if alloc_index != -1 and alloc_index<len(vack_frame):# leave rand frame along
                if vack_frame[alloc_index]=='1':
                    #advance data number here
                    data_num = data_num + 1 
                    go_on_flag = True
                    logger.info("Check last transmission: last time success")
                else:
                    go_on_flag = False
                    logger.info("Check last transmission: last time fail")
            else:
                go_on_flag = False
                logger.info("in rand frame, treat it as missing")

   

        node_amount = vfs_model.get_node_amount(payload)
        seed = vfs_model.get_seed(payload)
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
        last_node_amount = node_amount
        
       
        return (node_amount, seed, delta, vf_index, alloc_index, in_rand_frame, v_frame)


# /////////////////////////////////////////////////////////////////////////////
#                                   main
# /////////////////////////////////////////////////////////////////////////////


def main():
    
    #import protocol model
    vfs_model = VirtualFrameScheme(PacketType, NODE_SLOT_TIME)
    
    #node rx queue/event
    global node_rx_q, node_rx_sem, thread_run, alloc_index, last_node_amount, go_on_flag,file_input,\
           file_output, data, data_num
    node_rx_q = Queue.Queue(maxsize = NODE_RX_MAX)
    node_rx_sem = threading.Semaphore(NODE_RX_MAX) #up to the queue size
    thread_run = True 
    go_on_flag = True
    alloc_index = -1
    last_node_amount = -1
    data = "**heLLo**" # default data str
    data_num = 0




    for i in range(NODE_RX_MAX): # make all semaphore in 0 status
        node_rx_sem.acquire()

    def send_pkt(payload='', eof=False):
        return tb.txpath.send_pkt(payload, eof)

    global n_rcvd, n_right
        
    n_rcvd = 0
    n_right = 0

    def rx_callback(ok, payload):
        global n_rcvd, n_right
        n_rcvd += 1
        
        # Filter out incorrect pkt
        if ok:

            thingy = decode_common_pkt_header(tb,payload)
            if not thingy:
                return 
            (pktno,pkt_timestamp,pkt_type) = thingy

            n_right += 1
            now_ts = tb.sink.get_time_now().get_real_secs()
            node_rx_q.put(payload)
        else:
            logger.warning("Packet fail. Drop pkt!")
           
        return

    parser = OptionParser(option_class=eng_option, conflict_handler="resolve")
    expert_grp = parser.add_option_group("Expert")
#    parser.add_option("","--discontinuous", action="store_true", default=False,
#                      help="enable discontinuous")
    parser.add_option("","--from-file", default=None,
                      help="input file of samples")
#    parser.add_option("-M", "--megabytes", type="eng_float", default=1.0,
#                      help="set megabytes to transmit [default=%default]")
    parser.add_option("-s", "--size", type="eng_float", default=400,
                      help="set packet size [default=%default]")
    parser.add_option("-p", "--packno", type="eng_float", default=0,
                      help="set packet number [default=%default]")
    parser.add_option("","--to-file", default=None,
                      help="Output file for modulated samples")
    parser.add_option("","--bs", default=None,
                      help="assign if bs")

    transmit_path.add_options(parser, expert_grp)
    digital.ofdm_mod.add_options(parser, expert_grp)
    uhd_transmitter.add_options(parser)

    receive_path.add_options(parser, expert_grp)
    uhd_receiver.add_options(parser)
    digital.ofdm_demod.add_options(parser, expert_grp)

    (options, args) = parser.parse_args ()

    # Decide is BS or Node role
    IS_BS_ROLE = bool(options.bs)
    
    if options.from_file is None:
        if options.rx_freq is None:
            sys.stderr.write("You must specify -f FREQ or --freq FREQ\n")
            parser.print_help(sys.stderr)
            sys.exit(1)
    if options.packno is not None:
        packno_delta = options.packno
        logger.info("assign pktno start: %d" % packno_delta)

    # build the graph
    tb = my_top_block(rx_callback, options)

    # USRP device aligns with PC time (NTP)
    pc_now = time.time()
    tb.sink.set_time_now(uhd.time_spec(pc_now))
    tb.source.set_time_now(uhd.time_spec(pc_now))
    now_ts = tb.sink.get_time_now().get_real_secs()
    logger.info("\n{} Adjust to PC time: {}\n".format(
                str(datetime.fromtimestamp(time.time())), str(datetime.fromtimestamp(now_ts))))

    # get this node id
    NODE_ID = tb.sink.get_usrp_mboard_serial()
    # Append to required length
    NODE_ID = NODE_ID.zfill(NODE_ID_LEN)
    assert len(NODE_ID) == NODE_ID_LEN, "USRP NODE_ID {} len must be {}".format(NODE_ID, NODE_ID_LEN)
    logger.info("\nNODE ID: {}".format(NODE_ID))

    #realtime scheduling
    r = gr.enable_realtime_scheduling()
    if r != gr.RT_OK:
        logger.warn( "Warning: failed to enable realtime scheduling")

    # node, open input file if assigned
    if not IS_BS_ROLE and (options.from_file is not None):
        try:
            file_input = open("test.txt", "r")
            #data = file_input.read(2)
            logger.info( "Input file opened successfully")
        except:
            logger.error( "Error: file not exist")
 

    # bs, open output file if assigned
    if IS_BS_ROLE and (options.to_file is not None):
        try:
            file_output = open(options.to_file, "w+")
            logger.info( "Output file opened successfully")
        except:
            logger.error( "Error: file not exist")

    tb.start()                      # start flow graph

    n = 0
    pktno = 0
    pkt_size = int(options.size)


    def threadjob(stop_event,pktno,IS_BS,NODE_ID):
        global thread_run, data, go_on_flag, data_num
        print "Please start host now..."
        boot_time = time.time()
        bs_start_time = 0
        nd_start_time = 0
        nd_in_response = False
        time_data_collecting = len(TEST_NODE_LIST)*NODE_SLOT_TIME
        time_wait_for_my_slot = 0
        #while not stop_event.is_set():
        while thread_run:    
            if IS_BS:
                if time.time() > (bs_start_time + time_data_collecting):
                    print "...Frame start..."
                    #elapsed_time = time.time() - start_time            
                    #prepare
                    vfs_model.generate_seed_v_frame_rand_frame(TEST_NODE_LIST)
                    #send boardcast
                    vfs_model.send_dummy_pkt(tb) # hacking, send dummy pkt to avoid data lost
                    vfs_model.broadcast_vfs_pkt(tb, pkt_size, len(TEST_NODE_LIST),pktno+int(packno_delta))
                    pktno += 1
                    bs_start_time = time.time()
                  
                else:
                    pass
                    #vfs_model.send_dummy_pkt(tb)
                    
                    

            else: #node
                
                if (nd_in_response) and (time.time() > (nd_start_time + time_wait_for_my_slot)):
                    
                    #prepare data 
                    if go_on_flag : # get next data
                        print "onhand {},going to get next data".format(data)
                        try:  
                            data = file_input.read(2)
                            if data == '':
                                thread_run = False
                                return
                                #break
                                                    
                            print "read current data {}".format(data)

                        except:
                            #error end 
                            thread_run = False
                            tb.txpath.send_pkt(eof=True)
                     
                    else: # resend last data
                        logger.info( "resend data {}".format(data)) 

                    vfs_model.send_dummy_pkt(tb)# hacking, send dummy pkt to avoid data lost
                    vfs_model.send_vfs_pkt( NODE_ID, tb, pkt_size, data, data_num, pktno)
                    logger.info( "\n===========================\npktno:{}\npkt_size:{}\ndata numer:{}\ndata:{}\n===========================".format(pktno,pkt_size,data_num,data)) 

                    pktno += 1
                    nd_in_response = False
                else:
                    #print "nd_in_response{}, time {} > {} ".format(nd_in_response,time.time(), (nd_start_time + time_wait_for_my_slot))
                    pass
                    #vfs_model.send_dummy_pkt(tb)
                    #tb.txpath.send_pkt(eof=True)
                    
            #while node_rx_sem.acquire(False):   
            if not node_rx_q.empty():
                payload = node_rx_q.get()
                if payload: 
                    #here we need to decode the payload first
                    if IS_BS:
                        thingy = action(tb, vfs_model, payload, NODE_ID)
                        if thingy:
                            (delta, node_id, pktno, upload_data, data_number) = thingy
                            #check the data number in payload

                            if vfs_model.check_data_num(node_id,data_number):
                                logger.info("write file {}".format(upload_data))
                                vfs_model.set_data_num(node_id,data_number+1 & 0xffff) #keep track in vfs module
                                try:
                                    file_output.write(upload_data)
                                except:
                                    logger.info("write file fail")
                            else:
                                logger.info("incorrect data number")
                        else:
                            logger.info("BS decode payload fail")
                                

                    else:
                        print "... get broadcast ..."
                        thingy = action(tb, vfs_model, payload,NODE_ID)
                
                        if thingy:
                            (node_amount, seed, delta, vf_index, alloc_index, in_rand_frame, v_frame) = thingy
                            time_wait_for_my_slot = alloc_index * NODE_SLOT_TIME
                            logger.info( "I will upload at slot {}, wait for {}s".format(alloc_index,time_wait_for_my_slot))
                            nd_start_time = time.time()
                            nd_in_response = True
                            #vfs_model.send_vfs_pkt( NODE_ID, tb, pkt_size, "**heLLo**{}".pktno, pktno)
                        else:
                            logger.warn( "error during decode VFS_BROADCAST")
            
            #node_rx_sem.release 
  
    thread_event = threading.Event()
    thread = threading.Thread(target = threadjob, args = (thread_event,pktno,IS_BS_ROLE,NODE_ID))
    thread.daemon = True #make it a daemon thread
    thread_run = True
    thread.start()

    
    send_pkt(eof=True)
    time.sleep(2)               # allow time for queued packets to be sent
    tb.wait()                       # wait for it to finish
    thread_run = False
    while thread.isAlive():
        time.sleep(1)   

    try:
        file_input.close()
    except:
        pass
    try:
        file_output.close() 
    except:
        pass   
    print "join done"
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print "Interrupt"
        pass
