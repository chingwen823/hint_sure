import hashlib
import random
import math
import time
import binascii


id = '9772919416396094532'    # Target ID
Target_index = 10   # Target index

def check(frame_number = 0): #Decide participate this round or not
    
    if frame_number % Target_index == 0 :
        return 1
    else :
        return 0

def VF_mapping(seed = 0, f = 0):

    hash_result = hashlib.pbkdf2_hmac('sha256', id, seed, 100000)
    hash_result = binascii.hexlify(hash_result)
    hash_value = int(hash_result,16)
    VF_index = hash_value % f
    return VF_index

def Alloc_mapping(VF = 0, VF_index = 0):

    if VF[VF_index] == 1:

        Alloc_index = -1
        for i in range(VF_index+1):

            if VF[i] == 1:
                Alloc_index = Alloc_index + 1
            else :
                Alloc_index = Alloc_index
                
    else:
        Alloc_index = 'Rand'

    return Alloc_index

def transmit_slot(Alloc_index = 0, len = 0):
    
    count = 0

    for i in range(len):
        
        if count == Alloc_index:
            
            data = 'send data at slot ' + str(Alloc_index)
            print data
            break;
        
        else:
            
            time.sleep(1)
            print count, ' slot'
            count = count + 1
            
    if i == len-1:
        
        data = 'send data at slot Rand'
        print data

if __name__ == '__main__':

    # unpack payload here (frame_number + seed + VF)
    frame_number = 10
    seed = '17244409941231255664'
    VF = [1, 0, 1, 0, 0, 1, 1, 0]

    if check(frame_number) :    # Input is frame_number
        
        VF_index = VF_mapping(seed , len(VF)) # Hash(ID,Seed) mod f, Input is seed & length(VF)
        print 'VF index is: ', + VF_index

        Alloc_index = Alloc_mapping(VF,VF_index)   # Calculate number of '1' or 'Rand', #Input is VF & VF_index
        print 'Alloc index is: ', + Alloc_index

        transmit_slot(Alloc_index, VF.count(1)) # Do transmission at the slot       
        
    else :
        data = 'No participate this round'
        print data
        

        
    
