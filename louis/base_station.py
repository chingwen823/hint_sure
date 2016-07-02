import hashlib
import random
import math
import time
import binascii
        
# Target ID = 9772919416396094532

id_list = []
    
def generate_id():
    
    id = str(random.getrandbits(64))    # Generate ID
    id_list.append(id)

def generate_VF(frame_number):
    
    for i in range (1000):    # Total 1000 frames
        
        participate = []    # Participate list
        participate_count = 0  
        frame = {}  # The info are going to send to UE
        
        for j in range(500): # Decide which devices need to uplink in this frame, if its number is the factor of the frame number !

            if (i+1) % (j+1) == 0:
                participate_count = participate_count + 1   # Calculate how many devices in this frame
                participate.append(id_list[j])  # Record participate ID
     
        singleton_rate = 0
        round_count = 0
        final_VF_mapping = []
        final_virtual_frame = [0]*participate_count*2    # Generate empty VF 
        final_singleton_rate = 0
        seed = 0
        
        while singleton_rate < 0.75 and round_count < 20 : # Singleton rate below T, keep searching seed

            if singleton_rate > final_singleton_rate :  # in 20 rounds, choose the max as result

                final_VF_mapping = VF_mapping
                final_virtual_frame = virtual_frame
                final_singleton_rate = singleton_rate
                final_seed = seed

            VF_mapping = []
            virtual_frame = [0]*participate_count*2    # Generate empty VF
            seed = str(random.getrandbits(64))
            
            for k in range(len(participate)):

                hash_result = hashlib.pbkdf2_hmac('sha256', participate[k], seed, 100000)
                hash_result = binascii.hexlify(hash_result)
                hash_value = int(hash_result,16)
                index = hash_value % len(virtual_frame) # Do mod
                virtual_frame[index] = virtual_frame[index] + 1
                tup = (participate[k],str(index))   # Record the ID's index of VF
                VF_mapping.append(tup)
 
            singleton_count = 0.0  # To convert to float type
            
            for k in range(len(virtual_frame)):
                
                if virtual_frame[k] == 1 :  #singleton slot
                    singleton_count = singleton_count + 1
                elif virtual_frame[k] > 1 : #collision slot
                    virtual_frame[k] = 0
       
            singleton_rate = singleton_count / len(participate) # Calculate the singleton rate
            round_count = round_count + 1

        if singleton_rate > final_singleton_rate :  #jump out the loop, do check once again 

            final_VF_mapping = VF_mapping
            final_virtual_frame = virtual_frame
            final_singleton_rate = singleton_rate
            final_seed = seed;
            
        tup = (final_seed, final_virtual_frame) 
        temp = {str(i) : tup}
        frame.update(temp)  # The data struct is {frameno. : ((round_count,seed), VF)}

        Alloc_mapping = []  # Record the ID's index of Alloc

        for j in range(len(participate)):
            for k in range(len(final_VF_mapping)):
                if participate[j] == final_VF_mapping[k][0]:
                    if final_virtual_frame[int(final_VF_mapping[k][1])] == 1:
                        
                        Alloc_count = 0
                        
                        for w in range(int(final_VF_mapping[k][1])):
                            if final_virtual_frame[w] == 1:
                                
                                Alloc_count = Alloc_count + 1
                                
                        tup = (participate[j],str(Alloc_count))
                        Alloc_mapping.append(tup)
                        
                    else :
                        
                        tup = (participate[j],'rand')
                        Alloc_mapping.append(tup)
        
        print 'frame: ', frame
        print 'VF: ', final_VF_mapping
        print 'Alloc Frame: ', Alloc_mapping, '\n'

if __name__ == '__main__':

    for i in range(500):
        
        generate_id()
        
    id_list[9] = '9772919416396094532'  # Target ID
    
    frame_number = 0
    for i in range(1000):
    
        generate_VF(frame_number)
        frame_number = frame_number + 1


