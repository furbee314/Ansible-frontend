#!/usr/bin/env bash
echo "cockpit: $(systemctl is-active cockpit)"
echo "9090 listening: $(ss -ltn | grep -c ':9090')"
node --check /usr/share/cockpit/ansible-deployer/ansible-deployer.js && echo "installed JS syntax OK"
python3 -m py_compile /usr/share/cockpit/ansible-deployer/ansible_deployer.py && echo "installed py syntax OK"
echo "deployer group members: $(getent group deployer | cut -d: -f4)"
echo "deploy key perms: $(stat -c '%a %U:%G' /etc/ansible-deployer/deploy_ed25519)"
python3 -c "import json;print('config deploy_key:', json.load(open('/etc/ansible-deployer/config.json'))['deploy_key'])"
