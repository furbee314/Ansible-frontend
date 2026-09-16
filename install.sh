#!/usr/bin/env bash
# Install the Ansible Deployer Cockpit module.
# Usage: sudo ./install.sh [install|uninstall|test]
set -euo pipefail

MODULE=ansible-deployer
DEST=/usr/share/cockpit/$MODULE
CFG_DIR=/etc/ansible-deployer
HERE="$(cd "$(dirname "$0")" && pwd)"

need_root() {
    [ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
}

do_install() {
    need_root
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
    for f in index.html manifest.json po.js ansible-deployer.css ansible-deployer.js ansible_deployer.py; do
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
    *) echo "usage: $0 [install|uninstall|test]" >&2; exit 2 ;;
esac
