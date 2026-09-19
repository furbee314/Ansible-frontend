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
   **Multiple playbooks can be selected at once** (checkboxes); they run
   one after another in the order selected, and a failing playbook does
   not stop the rest — the job reports a "partial success" status.
3. **Credentials** — the kickstart-baseline `agent` user is pre-filled from
   `/etc/ansible-deployer/config.json`.
   - Password left blank → SSH key auth. The module runs as the logged-in
     Cockpit user (not root), so it uses the shared `deploy_key` from the
     config (see "Access model"), offered via `ANSIBLE_SSH_ARGS`.
   - Password entered → passed to ansible as `ANSIBLE_SSH_PASS` (and
     `ANSIBLE_BECOME_PASS` for sudo password); works with or without
     `sshpass` since ansible 2.5+ natively supports the pass env vars.
4. **Deploy** — runs `ansible -m ping` preflight against the chosen hosts,
   then `ansible-playbook` for **each selected playbook, in order**, with a
   per-deployment inventory. Output streams live into the page; Stop/Close
   available. A failing playbook is logged with its exit code and the
   remaining playbooks still run (job badge shows "partial success").

## Access model (no admin needed)

The module spawns the controller with `superuser: false` — it runs as the
**logged-in Cockpit user**, not root. Everything the module does on the
control host (scan, read playbooks, read the config, run ansible) is
readable/runnable by a normal user, so **no polkit/root prompt appears**.

The one secret the module needs is the **deploy SSH key**, used to reach the
target hosts. Access is therefore gated on reading that key:

- `install.sh` creates a `deployer` group and a shared key
  `/etc/ansible-deployer/deploy_ed25519` (`root:deployer`, mode `0640`), and
  records it in `config.json` as `deploy_key`.
- A user **in the `deployer` group** can read the key → `check` reports
  `key_ok: true` → the Deploy button enables.
- A user **not in the group** can't read it → `check` reports `key_ok: false`
  and the UI shows a banner ("Deploy is disabled: deploy key exists but is
  not readable by you") instead of failing mid-run.

Target-side privilege (`become`/sudo in STIG tasks) still comes from the
**sudo password** field in the UI (`ANSIBLE_BECOME_PASS`) — that is unrelated
to the local access gate.

To grant a user:

```bash
sudo usermod -aG deployer <user>            # read the key
ssh-copy-id -i /etc/ansible-deployer/deploy_ed25519.pub <user>@<target>  # trust the key on the target
# log the user in at https://host:9090/ansible-deployer/
```

Everything privileged still happens in the Python controller
(`ansible_deployer.py`); the JS frontend only drives the UI. Subcommands:
`scan`, `playbooks`, `check`, `deploy`.

## Install

```bash
dnf install -y ansible python3-pyyaml sshpass   # sshpass optional
sudo ./install.sh            # -> /usr/share/cockpit/ansible-deployer (+ deployer group + deploy key)
sudo ./install.sh access     # (re)provision group + key + config only
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
| `deploy_key` | path to the shared deploy SSH key (readable by the `deployer` group) |
| `deployer_group` | group whose members may read `deploy_key` (default `deployer`) |

## Repository layout

```
cockpit/            # the module (installed to /usr/share/cockpit/ansible-deployer/)
  manifest.json     #   menu entry
  index.html        #   page
  ansible-deployer.js / .css
  ansible_deployer.py  # controller (runs as logged-in user): scan / playbooks / check / deploy
config/config.json  # default configuration
install.sh          # install / uninstall / test / access
tests/              # controller smoke test + non-root / access verification
```

`cockpit/patternfly.css` is a vendored copy of the official PatternFly v6
bundle (self-contained: no build step required). It can be regenerated with
`cockpit/build.sh` (npm, optional) if you want to track a different PF version.

## Security notes

- Runs inside Cockpit's auth (your server login) but does **not** escalate to
  root locally — the controller runs as the logged-in user (`superuser: false`),
  so no polkit/root prompt appears. Deploy rights are gated on reading the
  shared `deploy_key` (the `deployer` group), not on admin status.
- The module is only reachable on port 9090, like the rest of Cockpit —
  don't expose it publicly without a VPN/reverse proxy.
- Job inventories and the generated wrapper playbooks both land in
  `/var/tmp/ansible-deployer-*` (world-writable, so a non-root user can create
  them) and are name-prefixed `__deployer_job_`; clean up `/var/tmp`
  periodically if that matters.
- The deploy key lives at `/etc/ansible-deployer/deploy_ed25519`
  (`root:deployer`, `0640`). Treat it like any other credential: only add
  trusted users to the `deployer` group, and rotate it (regenerate + re-add to
  target `authorized_keys`) if it's ever leaked.
- Don't put secrets in playbook `debug` output — it streams to the UI.

## Sample playbooks

`samples/` contains demo playbooks to try the UI with. Copy them into a
`playbook_dirs` directory on the target before deploying:

```
sudo cp samples/*.yml /opt/ansible-playbooks/
```

| File | What it does | Privileges |
|---|---|---|
| `sample_system_report.yml` | Prints OS/kernel/CPU/memory/date facts | none |
| `sample_service_status.yml` | Reports sshd/firewalld/chronyd state | none |
| `sample_marker_write.yml` | Writes a marker file to `/tmp` | none |
| `sample_stig_style_task.yml` | Bare STIG-style task file (auto-wrapped, `become: yes`) | sudo required |
