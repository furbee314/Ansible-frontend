#!/usr/bin/env bash
# Verify the non-root access model end-to-end, exactly as Cockpit will run it.
CTRL=/usr/share/cockpit/ansible-deployer/ansible_deployer.py
set -o pipefail

echo "=== live module state ==="
echo "cmd_check in py: $(grep -c 'cmd_check' "$CTRL")"
echo "require-refs in js (want 0): $(grep -c 'superuser: \"require\"' /usr/share/cockpit/ansible-deployer/ansible-deployer.js)"
echo "superuser:false in js: $(grep -c 'superuser: false' /usr/share/cockpit/ansible-deployer/ansible-deployer.js)"
echo "banner in index: $(grep -c 'ad-access-banner' /usr/share/cockpit/ansible-deployer/index.html)"
node --check /usr/share/cockpit/ansible-deployer/ansible-deployer.js && echo "node --check OK"
python3 -m py_compile "$CTRL" && echo "py_compile OK"

echo
echo "############ check (as agent, in deployer group) ############"
su - agent -c "python3 $CTRL check"

echo
echo "############ scan (as agent, non-root) ############"
su - agent -c "python3 $CTRL scan --subnets 10.10.10.0/24 --port 22 --timeout 1.0"

echo
echo "############ playbooks (as agent, non-root) ############"
su - agent -c "python3 $CTRL playbooks --dirs /opt/ansible-playbooks" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('count:',len(d)); [print('  ',x['id'],x['type']) for x in d]"

echo
echo "############ DEPLOY end-to-end (as agent, non-root, no sudo pw) ############"
echo "  target playbook = sample_marker_write.yml (writes /tmp, no become)"
su - agent -c "python3 $CTRL deploy --hosts 10.10.10.1 --user agent --port 22 --target /opt/ansible-playbooks/sample_marker_write.yml" 2>&1
echo "  deploy exit=$?"

echo
echo "############ marker file present on target? ############"
su - agent -c "ssh -i /etc/ansible-deployer/deploy_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes agent@10.10.10.1 'cat /tmp/ansible_deployer_sample_marker.txt'" 2>&1