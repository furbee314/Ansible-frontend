#!/usr/bin/env bash
# Reproduce the exact commands the browser fires, as a non-root user.
CTRL=/usr/share/cockpit/ansible-deployer/ansible_deployer.py
echo "=== playbooks BOTH dirs (as agent) — timed ==="
time su agent -c "python3 $CTRL playbooks --dirs /opt/ansible-playbooks,/opt/SHIBA-24" > /tmp/pb_out.json 2>/tmp/pb_err.txt
echo "exit=$?  stdout_bytes=$(wc -c < /tmp/pb_out.json)  stderr_bytes=$(wc -c < /tmp/pb_err.txt)"
head -c 200 /tmp/pb_err.txt; echo
python3 -c "
import json
d = json.load(open('/tmp/pb_out.json'))
print('parsed OK, entries:', len(d))
from collections import Counter
print(Counter(x['type'] for x in d))
"
echo
echo "=== scan exact browser args (as agent) — timed ==="
time su agent -c "python3 $CTRL scan --subnets 10.10.10.0/24 --port 22" > /tmp/scan_out.json 2>&1
echo "exit=$?"; cat /tmp/scan_out.json | head -c 300; echo
echo
echo "=== journal: cockpit activity in the last 2h (spawn/errors) ==="
journalctl -u cockpit -u cockpit.socket --since "-2h" -q 2>/dev/null | grep -iE 'error|fail|denied|spawn|ansible' | tail -30
echo "(end journal)"
