#!/usr/bin/env bash
# Smoke test for the ansible_deployer.py controller (run as root).
set -u
CTRL=/usr/share/cockpit/ansible-deployer/ansible_deployer.py
[ -f "$CTRL" ] || CTRL=/opt/ansible-deployer/src/ansible_deployer.py
fail=0

echo "=== scan 10.10.10.0/24 ==="
python3 "$CTRL" scan --subnets 10.10.10.0/24 --timeout 1.5 | python3 -c "
import json,sys
h=json.load(sys.stdin)
assert isinstance(h,list) and all('ip' in x for x in h), h
print('OK: %d host(s): %s' % (len(h), ', '.join(x['ip'] for x in h[:10])))
" || fail=1

echo "=== playbooks ==="
python3 "$CTRL" playbooks --dirs /opt/ansible-playbooks,/opt/SHIBA-24 | python3 -c "
import json,sys
p=json.load(sys.stdin)
assert len(p) > 100, len(p)
from collections import Counter
print('OK: %d entries, %s' % (len(p), dict(Counter(x['type'] for x in p))))
" || fail=1

echo "=== deploy (playbook, key auth; needs /opt/ansible-playbooks/smoke_test.yml) ==="
TARGET=/opt/ansible-playbooks/smoke_test.yml
[ -f "$TARGET" ] || { echo "skip: $TARGET not present"; exit 0; }
DEPLOY_OUT=$(DEPLOYER_SSH_PASSWORD= python3 "$CTRL" deploy \
    --hosts 10.10.10.1 --user agent --port 22 \
    --target "$TARGET" 2>&1)
RC=$?
echo "$DEPLOY_OUT" | tail -6
if [ $RC -eq 0 ]; then echo "OK: deploy exit 0"; else
    echo "FAIL: deploy exit $RC"; fail=1
fi

echo
[ $fail -eq 0 ] && echo "ALL TESTS PASSED" || echo "TESTS FAILED"
exit $fail
