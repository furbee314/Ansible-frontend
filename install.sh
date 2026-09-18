#!/usr/bin/env bash
# Install the Ansible Deployer Cockpit module.
# Usage: sudo ./install.sh [install|uninstall|test|access]
set -euo pipefail

MODULE=ansible-deployer
DEST=/usr/share/cockpit/$MODULE
CFG_DIR=/etc/ansible-deployer
HERE="$(cd "$(dirname "$0")" && pwd)"

# Access model: the module runs as the LOGGED-IN Cockpit user (superuser:false),
# NOT root. To deploy it needs to read a deploy SSH key. We provision:
#   * a `deployer` group
#   * a shared deploy key /etc/ansible-deployer/deploy_ed25519 (root:deployer, 0640)
#   * config.json deploy_key pointing at it
# Only users added to `deployer` can read the key, so only they can deploy.
DEPLOY_KEY="$CFG_DIR/deploy_ed25519"
DEPLOYER_GROUP=deployer

need_root() {
    [ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
}

# Idempotently create the deploy group + shared key and seed config.
do_access() {
    echo ">> access: provisioning deployer group + shared deploy key"
    getent group "$DEPLOYER_GROUP" >/dev/null || groupadd "$DEPLOYER_GROUP"
    install -d "$CFG_DIR"
    if [ ! -f "$DEPLOY_KEY" ]; then
        ssh-keygen -t ed25519 -f "$DEPLOY_KEY" -N "" \
            -C "ansible-deployer@$(hostname)" -q
        echo "   created deploy key $DEPLOY_KEY"
    else
        echo "   deploy key already present ($DEPLOY_KEY)"
    fi
    chown root:"$DEPLOYER_GROUP" "$DEPLOY_KEY"
    chmod 0640 "$DEPLOY_KEY"
    [ -f "$DEPLOY_KEY.pub" ] && chmod 0644 "$DEPLOY_KEY.pub"

    # Seed deploy_key / deployer_group into config.json (create if absent).
    python3 - "$CFG_DIR/config.json" "$DEPLOY_KEY" "$DEPLOYER_GROUP" <<'PY'
import json, sys, os
path, key, group = sys.argv[1], sys.argv[2], sys.argv[3]
if os.path.exists(path):
    with open(path) as f:
        try: cfg = json.load(f)
        except Exception: cfg = {}
else:
    cfg = {}
cfg["deploy_key"] = key
cfg["deployer_group"] = group
if not os.path.exists(path):
    cfg.setdefault("scan_subnets", ["10.10.10.0/24"])
    cfg.setdefault("playbook_dirs", ["/opt/ansible-playbooks", "/opt/SHIBA-24"])
    cfg.setdefault("default_creds", {"user": "agent", "password": "",
                                     "sudo_password": "", "port": 22})
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("   config.json: deploy_key=%s deployer_group=%s" % (key, group))
PY
    echo
    echo "Next steps to grant a user access:"
    echo "  1. usermod -aG $DEPLOYER_GROUP <user>          # let them read the key"
    echo "  2. ssh-copy-id -i $DEPLOY_KEY.pub <user>@<target>   # trust the key"
    echo "     (or append $(basename "$DEPLOY_KEY").pub to the target's authorized_keys)"
    echo "  3. log the user in at https://$(hostname):9090/ansible-deployer/"
}

do_install() {
    need_root
    # Access provisioning first (group + deploy key + config) so the module
    # is usable by non-admin users the moment it's installed.
    do_access
    echo ">> checking dependencies"
    local missing=()
    command -v ansible >/dev/null || missing+=("ansible")
    command -v ansible-playbook >/dev/null || missing+=("ansible-playbook")
    python3 -c "import yaml" 2>/dev/null || missing+=("python3-yaml (PyYAML)")
    command -v sshpass >/dev/null || echo "   (optional) sshpass not found — password auth will be unavailable"
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "Missing dependencies: ${missing[*]}"
        echo "Install them, e.g.: dnf install -y python3-ansible ansible python3-pyyaml sshpass"
        exit 1
    fi

    echo ">> installing module to $DEST"
    install -d "$DEST" "$CFG_DIR"
    for f in index.html manifest.json po.js ansible-deployer.css ansible-deployer.js ansible_deployer.py patternfly.css; do
        install -m 0644 "$HERE/cockpit/$f" "$DEST/$f"
    done
    install -m 0755 "$HERE/cockpit/ansible_deployer.py" "$DEST/ansible_deployer.py"

    if [ ! -f "$CFG_DIR/config.json" ]; then
        echo ">> creating default config $CFG_DIR/config.json"
        install -m 0644 "$HERE/config/config.json" "$CFG_DIR/config.json"
    fi

    echo ">> restarting cockpit to load the module"
    systemctl restart cockpit

    echo
    echo "Installed. Open https://this-host:9090/$MODULE/ in a browser,"
    echo "or find 'Ansible Deployer' in the Cockpit app menu."
    echo "Edit subnets / playbook dirs / default credentials in $CFG_DIR/config.json."
}

do_uninstall() {
    need_root
    echo ">> removing $DEST"
    rm -rf "$DEST"
    systemctl restart cockpit
    echo "Uninstalled. (Leaving $CFG_DIR/config.json in place.)"
}

do_test() {
    echo "Running controller smoke test..."
    bash "$HERE/tests/test-controller.sh"
}

case "${1:-install}" in
    install) do_install ;;
    uninstall) do_uninstall ;;
    test) do_test ;;
    access) need_root; do_access ;;
    *) echo "usage: $0 [install|uninstall|test|access]" >&2; exit 2 ;;
esac
