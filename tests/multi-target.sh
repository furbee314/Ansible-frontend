#!/bin/bash
# multi-target controller test as non-root
set -u
P=/opt/ansible-playbooks
BAD=/tmp/ansible-deployer-test-fail.yml
cat > "$BAD" <<'EOF'
- name: Deliberate failure (test)
  hosts: all
  tasks:
    - name: fail
      ansible.builtin.command: /bin/false
EOF
echo "=== T1: two good playbooks, non-root ==="
su - agent -c "python3 /usr/share/cockpit/ansible-deployer/ansible_deployer.py deploy \
  --hosts 10.10.10.1 --user agent --port 22 \
  --target $P/sample_system_report.yml,$P/sample_service_status.yml" 2>&1 | grep -E '\[deployer\]|PLAY RECAP' | tail -12
echo "EXIT=${PIPESTATUS[0]} (want 0)"
echo
echo "=== T2: one good + one failing playbook ==="
su - agent -c "python3 /usr/share/cockpit/ansible-deployer/ansible_deployer.py deploy \
  --hosts 10.10.10.1 --user agent --port 22 \
  --target $P/sample_service_status.yml,$BAD" 2>&1 | grep -E '\[deployer\]' | tail -10
echo "EXIT=${PIPESTATUS[0]} (want 1)"
echo
echo "=== T3: nonexistent target ==="
su - agent -c "python3 /usr/share/cockpit/ansible-deployer/ansible_deployer.py deploy \
  --hosts 10.10.10.1 --user agent --port 22 --target /nope/missing.yml" 2>&1 | tail -3
echo "EXIT=${PIPESTATUS[0]} (want 2)"
rm -f "$BAD"; rm -rf /tmp/ansible-deployer-*

