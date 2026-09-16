# Ansible Deployer (Cockpit module)

A [Cockpit](https://cockpit-project.org) module to pick network hosts
(discovered live — no DNS or central inventory required) and deploy Ansible
playbooks that live on the server.

## What it does

1. **Host discovery** — "Scan" runs a concurrent SSH-banner sweep of the
   configured subnets (default `10.10.10.0/24`). Anything answering on port
   22 shows up in the UI with its banner.
2. **Playbook picker** — lists every `.yml/.yaml` under `playbook_dirs`
   (default `/opt/ansible-playbooks` and `/opt/SHIBA-24`), classified as
   `playbook` (plays with `hosts:`) or `task` (bare task lists). Bare task
   files are auto-wrapped in a generated thin playbook so
   `ansible-playbook` can run them. Filter by name or STIG id.
3. **Credentials** — the kickstart-baseline `agent` user is pre-filled from
   `/etc/ansible-deployer/config.json`.
   - Password left blank → SSH key auth (`~/.ssh/id_ed25519` is offered via
     `ANSIBLE_SSH_ARGS`).
   - Password entered → passed to ansible as `ANSIBLE_SSH_PASS` (and
     `ANSIBLE_BECOME_PASS` for sudo password); works with or without
     `sshpass` since ansible 2.5+ natively supports the pass env vars.
4. **Deploy** — runs `ansible -m ping` preflight against the chosen hosts,
   then `ansible-playbook` with a per-deployment inventory. Output streams
   live into the page; Stop/Close available.

Everything privileged happens in the root Python controller
(`ansible_deployer.py`, spawned via `cockpit.spawn({superuser: "require"})`);
the JS frontend only drives the UI.

## Install

```bash
dnf install -y ansible python3-pyyaml sshpass   # sshpass optional
sudo ./install.sh            # -> /usr/share/cockpit/ansible-deployer
sudo ./install.sh test       # controller smoke test
```

Then open `https://host:9090/ansible-deployer/` (or find *Ansible Deployer*
in the Cockpit app menu).

Uninstall: `sudo ./install.sh uninstall`.

## Configuration

`/etc/ansible-deployer/config.json` (template in `config/`):

| key | meaning |
|---|---|
| `scan_subnets` | CIDRs scanned by the host picker |
| `scan_port` | SSH port for discovery |
| `playbook_dirs` | directories walked for playbooks/tasks |
| `default_creds` | kickstart baseline (user, password, sudo_password, port) |

## Repository layout

```
cockpit/            # the module (installed to /usr/share/cockpit/ansible-deployer/)
  manifest.json     #   menu entry
  index.html        #   page
  ansible-deployer.js / .css
  ansible_deployer.py  # root controller: scan / playbooks / deploy
config/config.json  # default configuration
install.sh          # install / uninstall / test
tests/              # controller smoke test
```

## Security notes

- Runs inside Cockpit's auth (your server login; root actions via
  `superuser: "require"`, i.e. pkexec for non-root admins).
- The module is only reachable on port 9090, like the rest of Cockpit —
  don't expose it publicly without a VPN/reverse proxy.
- Job inventories land in `/var/tmp/ansible-deployer-*` (temp dirs) and
  wrapper playbooks next to the task file (name-prefixed
  `__deployer_job_`); neither is kept after the run in any meaningful way —
  clean up `/var/tmp` periodically if that matters.
- Don't put secrets in playbook `debug` output — it streams to the UI.
