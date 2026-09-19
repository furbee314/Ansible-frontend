#!/bin/bash
# cleanup post-deploy-fix verification
rm -f /tmp/ansible_deployer_sample_marker.txt
rm -f /tmp/ansible-deployer-* -r 2>/dev/null
rm -f /tmp/probe_*.py /tmp/cdp.py /tmp/step1.py /tmp/login_form.py /tmp/stream_probe.py \
      /tmp/stream_type.py /tmp/stream_type2.py /tmp/inspect_ck.py /tmp/fetch_ck*.py \
      /tmp/deploy_repro*.py /tmp/full_deploy*.py /tmp/iframe_deploy.py /tmp/verify_fix*.py \
      /tmp/mod_real.html /tmp/module_iframe.html /tmp/mod_served.html /tmp/mod_headers.txt \
      /tmp/cockpit.js /tmp/cockpit_live.js /tmp/live_ck.js 2>/dev/null
# stop the debug chromium
pkill -f 'remote-debugging-port=9222' 2>/dev/null
sleep 1
# remove throwaway test user
userdel -r uitest 2>/dev/null || true
echo "marker gone: $(ls /tmp/ansible_deployer_sample_marker.txt 2>&1)"
id uitest 2>&1 | head -1
pgrep -f 'remote-debugging-port=9222' | grep -v $$ | head -2 || echo "chromium stopped"
