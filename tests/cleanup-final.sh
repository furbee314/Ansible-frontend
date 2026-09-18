#!/usr/bin/env bash
# Post-verify cleanup + final coherence check.
set -o pipefail

# remove throwaway non-deployer test user
if id tester >/dev/null 2>&1; then userdel -r tester; fi
echo "tester removed: $(id tester 2>&1 | head -1)"

# remove stale generated job wrappers from prior root runs (safe: generated artifacts)
rm -f /opt/SHIBA-24/tasks/__deployer_job_*.yml
rm -f /opt/ansible-playbooks/__deployer_job_*.yml
rm -f /tmp/ansible_deployer_sample_marker.txt
rm -rf /var/tmp/ansible-deployer-*
echo "stale wrappers left: $(ls /opt/SHIBA-24/tasks/__deployer_job_* /opt/ansible-playbooks/__deployer_job_* 2>/dev/null | wc -l)"
echo "leftover /var/tmp job dirs: $(ls -d /var/tmp/ansible-deployer-* 2>/dev/null | wc -l)"

echo
echo "=== final: live module coherence ==="
echo "cockpit service: $(systemctl is-active cockpit)"
echo "port 9090 listening: $(ss -ltn 2>/dev/null | grep -c ':9090')"
node --check /usr/share/cockpit/ansible-deployer/ansible-deployer.js && echo "installed JS syntax OK"
python3 -m py_compile /usr/share/cockpit/ansible-deployer/ansible_deployer.py && echo "installed py syntax OK"

echo
echo "=== access model summary ==="
echo "deployer group members: $(getent group deployer | cut -d: -f4)"
echo "deploy key: $(ls -la /etc/ansible-deployer/deploy_ed25519 2>/dev/null)"
echo "config deploy_key: $(python3 -c "import json;print(json.load(open('/etc/ansible-deployer/config.json')).get('deploy_key'))")"
