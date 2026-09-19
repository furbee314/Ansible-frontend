#!/usr/bin/env bash
echo "=== cockpit service ==="
systemctl is-active cockpit
echo "=== module served? (curl the module index, expect 200 + our page) ==="
curl -sk -o /dev/null -w "HTTP %{http_code}\n" https://127.0.0.1:9090/ansible-deployer/
echo "=== installed manifest present ==="
python3 -c "import json;d=json.load(open('/usr/share/cockpit/ansible-deployer/manifest.json'));print('menu label:', d['menu']['index']['label'])"
echo "=== recent journal errors for cockpit? ==="
journalctl -u cockpit -u cockpit.socket --since "10 min ago" -p err -q 2>/dev/null | tail -5 || true
echo "(end)"
