# Ansible Deployer

Web UI to pick network hosts (discovered live — no DNS/central inventory
required) and deploy Ansible playbooks that live on this server.

## What it does

1. **Host discovery** — concurrent SSH-banner scan of configured subnets
   (default: `10.10.10.0/24`). Anything answering on port 22 shows up in the
   UI with its banner. Edit the subnet list on the page or in `config.json`.
2. **Playbook picker** — lists every `.yml/.yaml` under `playbook_dirs`
   (default: `/opt/ansible-playbooks` and `/opt/SHIBA-24`), classified as
   `playbook` (top-level plays with `hosts:`) or `task` (bare task lists).
   Bare task files are automatically wrapped in a generated playbook
   (`__deployer_job_<id>.yml` next to the task file, auto-cleaned after 3 h).
3. **Credentials** — the kickstart-default `agent` user is pre-filled.
   - **Password left blank** → key auth (`/root/.ssh/id_ed25519` is offered
     to target hosts via `ANSIBLE_SSH_ARGS`).
   - **Password entered** → `sshpass` is used (must be installed:
     `dnf install sshpass`).
   - User, port, and sudo password can be overridden per deployment.
4. **Deploy** — runs `ansible -m ping` preflight against the chosen hosts,
   then `ansible-playbook` with a generated one-host-per-job inventory.
   Live log streaming, job history, cancel (best-effort).

## Run

```bash
# install deps (RHEL 9)
dnf install -y python3-flask python3-waitress python3-yaml ansible sshpass

# point it at your playbook directories
# edit config.json -> playbook_dirs, scan_subnets, default_creds

# foreground (dev)
python3 /opt/ansible-deployer/app.py          # listens on 0.0.0.0:8082

# service
install -m 0644 ansible-deployer.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now ansible-deployer
```

Fresh clone: the service unit assumes the app lives at
`/opt/ansible-deployer`; either copy the repo there or adjust
`WorkingDirectory`/`ExecStart` in the unit. `requirements.txt` lists the
pip-side deps if you prefer a virtualenv over system packages.


## Configuration

`/opt/ansible-deployer/config.json`:

| key | meaning |
|---|---|
| `scan_subnets` | CIDRs scanned by the host picker |
| `scan_port` | SSH port for discovery |
| `playbook_dirs` | directories walked for playbooks/tasks |
| `default_creds` | kickstart baseline (user, password, sudo_password, port) |
| `listen_host` / `listen_port` | bind address |
| `auth_token` | reserved for future UI auth (currently unused) |

## Files

- `app.py` — Flask app: API + job runner (all in one, stdlib + flask + yaml)
- `web/index.html` — single-page UI
- `jobs/` — per-job inventory + log (kept for audit)
- `/opt/ansible-playbooks/` — your playbooks go here (or any dir in
  `playbook_dirs`)

## Security notes

- The UI binds `0.0.0.0:8082` and currently has **no auth gate** — keep it on
  a trusted LAN or put nginx in front with a Basic-Auth before exposing it.
- Job logs on disk contain whatever the playbooks print (avoid putting
  secrets in playbook `debug` tasks).
- Passwords are only ever passed to `sshpass` in the process environment of
  the job; they are not written to the inventory or the log.
