#!/bin/bash

kill $(ps aux | grep '[t]est_tx_rx.py' | awk '{print $2}')

exit 0
