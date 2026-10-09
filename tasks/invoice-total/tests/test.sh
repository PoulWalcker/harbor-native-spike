#!/bin/sh
set -eu
cd /tests
python3 -m native_spike.identity
exec python3 verify.py
