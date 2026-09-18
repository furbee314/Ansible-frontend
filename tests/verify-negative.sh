#!/usr/bin/env bash
# Negative + wrapper tests.
CTRL=/usr/share/cockpit/ansible-deployer/ansible_deployer.py
set -o pipefail

echo "=== create throwaway non-deployer user 'tester' ==="
if id tester >/dev/null 2>&1; then echo "tester already exists"; else
  useradd -m -s /bin/bash tester
fi
# ensure NOT in deployer
id -nG tester
echo
echo "=== NEGATIVE: tester NOT in deployer -> key unreadable -> check says key_ok:false ==="
su tester -c "python3 $CTRL check"

echo
echo "=== add tester to deployer, re-check -> key_ok:true ==="
usermod -aG deployer tester
id -nG tester
su tester -c "python3 $CTRL check"

echo
echo "=== BARE-TASK wrapper mechanism (no become): temp task file in /opt/SHIBA-24/tasks (root-owned) ==="
cat > /opt/SHIBA-24/tasks/__wrapper_probe.yml <<'YML'
---
- name: Wrapper probe (no become)
  debug:
    msg: "wrapper include path resolved OK"
YML
echo "  created /opt/SHIBA-24/tasks/__wrapper_probe.yml"
echo "  dir perms (root-owned, non-writable for users):"
ls -ld /opt/SHIBA-24/tasks
echo
echo "=== deploy the bare task AS agent (non-root) -> must write wrapper to /var/tmp, not to /opt/SHIBA-24/tasks ==="
su agent -c "python3 $CTRL deploy --hosts 10.10.10.1 --user agent --port 22 --target /opt/SHIBA-24/tasks/__wrapper_probe.yml" 2>&1
echo "  deploy exit=$?"
echo
echo "=== confirm wrapper was NOT written into the root-owned task dir ==="
ls /opt/SHIBA-24/tasks/ | grep -c '__wrapper_probe' || true
echo "(probe task itself + any wrapper; expect only the 1 probe we made, no __deployer_job_)"
ls /opt/SHIBA-24/tasks/__deployer_job_* 2>&1 | head
echo
echo "=== cleanup ==="
rm -f /opt/SHIBA-24/tasks/__wrapper_probe.yml
rm -rf /var/tmp/ansible-deployer-*
echo "done"