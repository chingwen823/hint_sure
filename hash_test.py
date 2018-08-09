import os, hashlib, binascii


def vfs(node_id_list, seed):
    v_frame = ['0'] * len(node_id_list) * 2
    for node_id in node_id_list:
        derived_key = hashlib.pbkdf2_hmac('sha256', node_id, seed, 10000)
        vf_index = derived_key % len(node_id_list)
        v_frame[vf_index] = '1'
    print "collision? {}".format(v_frame.count('1') != len(node_id_list))
    print "v_frame {}, seed {}, list {}".format(v_frame, seed, node_id_list)
    return v_frame.count('1') != len(node_id_list)


def ps(node_id_list, seed):
    v_frame = ['0'] * len(node_id_list)
    for node_id in node_id_list:
        derived_key = hashlib.pbkdf2_hmac('sha256', node_id, seed, 10000)
        hashed = binascii.hexlify(derived_key)
        hashed_bin = int(hashed, 16)
        vf_index = hashed_bin % len(node_id_list)
        v_frame[vf_index] = '1'
    print "collision? {}".format(v_frame.count('1') != len(node_id_list))
    print "v_frame {}, seed {}, list {}".format(v_frame, seed, node_id_list)
    return v_frame.count('1') != len(node_id_list)

def ps2(node_id_list):
    has_collision = True
    while has_collision:
        a_frame = ['0'] * len(node_id_list)
        salt = os.urandom(8)
        # Math hashing: h(Node ID, seed) = (Node ID XOR seed) mod node_amount
        for node_id in node_id_list:
            assert len(node_id) == len(salt), "Node ID len {} not equals to seed len {}".format(
                len(node_id), len(salt))
            # Python official: hashlib.<crpyto_hash> (openssl_<crypto_hash) is 3x faster than pbkdf2_hmac
            # derived_key = hashlib.pbkdf2_hmac('sha256', node_id, seed, 10000)
            # hashed = binascii.hexlify(derived_key)
            hashed = hashlib.sha256(node_id + salt).hexdigest()
            hashed_bin = int(hashed, 16)
            af_index = hashed_bin % len(node_id_list)
            a_frame[af_index] = node_id

        # Check if any calculated node index is collided
        #hell = any(n == '0' for n in a_frame)
        has_collision = any(n == '0' for n in a_frame)
        print "a_frame {}, seed {}, has_collision {}".format(a_frame, binascii.hexlify(salt), has_collision)


salt = os.urandom(8)

n1 = '00000001'
n2 = '00000002'
n3 = '00000003'
n4 = '00000004'
l = [n1,n2]

#ps(l, salt
ps2(l)
