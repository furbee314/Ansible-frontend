#!/usr/bin/env python3
"""
Ansible Deployer — web UI to pick network hosts and run server-side ansible
playbooks/tasks against them.

- Host discovery: concurrent SSH-banner scan of configured subnets (no
  DNS/central inventory required). Optional ping pre-filter.
- Playbook discovery: scans configured directories for *.yml/*.yaml playbooks
  and extracts play names (tags/vars are read via a dry-parse, never executed).
- Credentials: kickstart-default user/password from config.json are pre-filled;
  the UI can override user/password/port per deployment.
- Deploys run `ansible` / `ansible-playbook` as subprocesses, streaming stdout
  to a per-job log file; the UI polls /api/jobs/<id>/log.
"""
import concurrent.futures
import configparser
import datetime
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import uuid

import yaml
from flask import Flask, abort, jsonify, request, send_from_directory

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
WEB_DIR = os.path.join(APP_DIR, "web")
JOBS_DIR = os.path.join(APP_DIR, "jobs")


def load_config():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    defaults = {
        "listen_host": "0.0.0.0",
        "listen_port": 8082,
        "auth_token": "",
        "scan_subnets": ["10.10.10.0/24"],
        "scan_port": 22,
        "scan_timeout_s": 2.5,
        "playbook_dirs": ["/opt/ansible-playbooks", "/opt/SHIBA-24"],
        "default_creds": {"user": "agent", "password": "", "sudo_password": "", "port": 22},
        "use_ssh_key": True,
    }
    defaults.update(cfg)
    defaults.setdefault("default_creds", {})
    return defaults


CFG = load_config()
os.makedirs(JOBS_DIR, exist_ok=True)
for d in CFG["playbook_dirs"]:
    os.makedirs(d, exist_ok=True)

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")

# ---------------------------------------------------------------------------
# Host discovery
# ---------------------------------------------------------------------------

def ssh_banner(ip, port, timeout):
    """Return (banner_line, host_keys) if the port is open, else None."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            banner = s.recv(256).decode(errors="replace").strip()
        return banner or None
    except OSError:
        return None


def scan_subnets(subnets, port, timeout):
    results = []
    for net in subnets:
        try:
            net_obj = ipaddress.ip_network(net, strict=False)
            hosts = [str(h) for h in net_obj.hosts() if h.version == 4]
        except ValueError:
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(hosts), 256)) as pool:
            futs = {pool.submit(ssh_banner, h, port, timeout): h for h in hosts}
            for fut in concurrent.futures.as_completed(futs):
                h = futs[fut]
                banner = fut.result()
                if banner:
                    results.append({"ip": h, "ssh_banner": banner})
    results.sort(key=lambda r: [int(x) for x in r["ip"].split(".")])
    return results


def ping_host(ip, timeout):
    proc = subprocess.run(
        ["ping", "-c", "1", "-W", str(int(timeout)), ip],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Playbook registry (dry parse — nothing is executed here)
# ---------------------------------------------------------------------------


def list_playbooks():
    out = []
    for base in CFG["playbook_dirs"]:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in sorted(files):
                if not fn.endswith((".yml", ".yaml")):
                    continue
                path = os.path.join(root, fn)
                out.append(summarize_playbook(path))
    out.sort(key=lambda p: (p["source_dir"], p["id"]))
    return out


def summarize_playbook(path):
    """Parse one file, classify it, extract names — read-only, safe."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return {"id": os.path.basename(path), "path": path,
                "source_dir": "error", "type": "unknown", "plays": [],
                "stig_ids": [], "needs_wrap": False}

    base = os.path.basename(path)
    source_dir = next((d for d in CFG["playbook_dirs"]
                       if path.startswith(d + os.sep)), os.path.dirname(path))

    plays = []
    parsed = None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None

    if isinstance(parsed, list) and parsed:
        if any(isinstance(p, dict) and "hosts" in p for p in parsed):
            # standard playbook: top-level list of plays
            for item in parsed:
                if isinstance(item, dict):
                    plays.append({"id": str(item.get("tags", ["play"])[-1]
                                    if item.get("tags") else "play"),
                                  "name": str(item.get("name", "play")),
                                  "hosts": str(item.get("hosts", "")),
                                  "tags": item.get("tags") or []})
        elif all(isinstance(p, dict) and "name" in p for p in parsed):
            # bare task file: top-level list of tasks
            for item in parsed:
                tags = item.get("tags") or []
                plays.append({"id": tags[-1] if tags else "task",
                              "name": str(item["name"]),
                              "hosts": "", "tags": tags})

    if not plays:
        m = re.search(r"STIG ID:\s*(\S+)", text)
        if m:
            plays.append({"id": m.group(1), "name": m.group(1),
                          "hosts": "", "tags": []})

    ptype = ("playbook" if any(p["hosts"] for p in plays)
             else ("task" if plays else "other"))
    stigs = sorted(set(re.findall(r"UBTU[-_]24[-_]?\d*", text)))[:50]
    return {"id": base, "path": path, "source_dir": source_dir,
            "type": ptype, "plays": plays, "stig_ids": stigs,
            "needs_wrap": ptype == "task"}


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

JOBS = {}
JOBS_LOCK = threading.Lock()


def run_job(job_id, hosts, creds, target, inventory_path, log_path, run_target=None):
    user = creds.get("user", "")
    port = int(creds.get("port") or 22)
    password = creds.get("password") or ""
    sudo_pw = creds.get("sudo_password") or ""

    env = dict(os.environ,
               ANSIBLE_HOST_KEY_CHECKING="False",
               ANSIBLE_DEPRECATION_WARNINGS="False",
               ANSIBLE_STDOUT_CALLBACK="default")

    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    key = os.path.expanduser("~/.ssh/id_ed25519")
    if os.path.exists(key):
        env["ANSIBLE_SSH_ARGS"] += " -i %s" % key
    if password:
        sshpass = shutil.which("sshpass")
        if not sshpass:
            with open(log_path, "a") as f:
                f.write("[deployer] ERROR: sshpass not found; install it "
                        "(dnf install sshpass) to use password auth.\n")
            with JOBS_LOCK:
                JOBS[job_id]["status"] = "error"
            return
        sshpass_args = [sshpass, "-p", password]
    else:
        # no password: fall back to key-based auth (id_ed25519 above, if present)
        sshpass_args = []

    # preflight connectivity check: the job inventory only contains the
    # selected hosts, so "all" == the chosen host list
    cmd = ["ansible", "-i", inventory_path, "all",
           "-m", "ping", "-o"]
    # preflight connectivity check
    with open(log_path, "a") as f:
        f.write("\n[preflight] ansible -i %s all -m ping\n" % inventory_path)
    p = subprocess.run(sshpass_args + cmd, env=env,
                       stdout=open(log_path, "ab"), stderr=subprocess.STDOUT,
                       timeout=300, stdin=subprocess.DEVNULL)
    with JOBS_LOCK:
        if JOBS[job_id]["status"] != "cancelled":
            JOBS[job_id]["preflight_ok"] = (p.returncode == 0)
    if p.returncode != 0:
        with open(log_path, "a") as f:
            f.write("\n[preflight] FAILED — connectivity check did not succeed. "
                    "Aborting before running the playbook.\n")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
        return

    cmd = ["ansible-playbook", "-i", inventory_path, run_target or target]
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "running"
    p = subprocess.run(sshpass_args + cmd, env=env,
                       stdout=open(log_path, "ab"), stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL)
    with open(log_path, "a") as f:
        f.write("\n[deployer] ansible-playbook exit code: %d\n" % p.returncode)
    with JOBS_LOCK:
        if JOBS[job_id]["status"] == "running":
            JOBS[job_id]["status"] = "success" if p.returncode == 0 else "error"
        JOBS[job_id]["finished_at"] = time.time()


def _cleanup_old_wrappers(tdir):
    import glob
    cutoff = time.time() - 3 * 3600
    for w in glob.glob(os.path.join(tdir, "__deployer_job_*.yml")):
        try:
            if os.path.getmtime(w) < cutoff:
                os.unlink(w)
        except OSError:
            pass


def create_job(hosts, creds, target):
    job_id = uuid.uuid4().hex[:12]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    inventory_path = os.path.join(JOBS_DIR, "%s_inventory.ini" % job_id)
    log_path = os.path.join(JOBS_DIR, "%s_log.txt" % job_id)

    hosts = sorted(set(hosts))
    with open(inventory_path, "w") as f:
        f.write("[targets]\n")
        for h in hosts:
            f.write("%s ansible_port=%s ansible_user=%s\n"
                    % (h, creds.get("port") or 22, creds.get("user")))

    run_target = target
    if summarize_playbook(target)["needs_wrap"]:
        # bare task file: write a thin wrapper playbook next to it
        # (include_tasks resolves relative to the playbook's own dir)
        tdir, tname = os.path.dirname(target), os.path.basename(target)
        wrapper = os.path.join(tdir, "__deployer_job_%s.yml" % job_id)
        with open(wrapper, "w") as f:
            f.write("# generated by ansible-deployer (job %s) — safe to delete\n"
                    % job_id)
            f.write("- name: Deployer job %s\n" % job_id)
            f.write("  hosts: all\n  become: yes\n")
            f.write("  tasks:\n    - name: Include STIG task %s\n" % tname)
            f.write("      ansible.builtin.include_tasks: %s\n" % tname)
        run_target = wrapper
        _cleanup_old_wrappers(tdir)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "created_at": time.time(),
            "status": "queued",
            "hosts": hosts,
            "target": target,
            "user": creds.get("user"),
            "port": creds.get("port") or 22,
            "auth": "password" if creds.get("password") else "ssh-key",
            "log_path": log_path,
            "log_offset": 0,
            "preflight_ok": None,
        }

    t = threading.Thread(target=run_job, args=(job_id, hosts, creds,
                                               target, inventory_path, log_path,
                                               run_target),
                         daemon=True)
    t.start()
    return JOBS[job_id]


def cancel_job(job_id):
    """Best-effort: mark cancelled; the in-flight process will finish its current
    task but the job will be flagged so the UI shows it as cancelled."""
    with JOBS_LOCK:
        if job_id in JOBS and JOBS[job_id]["status"] in ("queued", "running"):
            JOBS[job_id]["status"] = "cancelled"
            return True
    return False


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/config")
def api_config():
    return jsonify({"default_creds": dict(CFG["default_creds"], password=""),
                    "scan_subnets": CFG["scan_subnets"],
                    "use_ssh_key": CFG["use_ssh_key"],
                    "auth_required": bool(CFG["auth_token"])})


@app.post("/api/scan")
def api_scan():
    body = request.get_json(silent=True) or {}
    subnets = body.get("subnets") or CFG["scan_subnets"]
    port = int(body.get("port") or CFG["scan_port"])
    timeout = float(body.get("timeout") or CFG["scan_timeout_s"])
    results = scan_subnets(subnets, port, timeout)
    return jsonify({"hosts": results, "scanned": len(subnets)})


@app.get("/api/playbooks")
def api_playbooks():
    return jsonify({"playbooks": list_playbooks()})


@app.post("/api/deploy")
def api_deploy():
    body = request.get_json(silent=True) or {}
    hosts = body.get("hosts") or []
    creds = body.get("creds") or {}
    target = body.get("target") or ""
    if not hosts or not target or not os.path.exists(target):
        return jsonify({"error": "hosts, target (playbook path) required"}), 400
    if not creds.get("user"):
        return jsonify({"error": "ssh user required"}), 400
    job = create_job(hosts, creds, target)
    return jsonify(job)


@app.get("/api/jobs")
def api_jobs():
    with JOBS_LOCK:
        jobs = [dict(j, log_offset=None) for j in JOBS.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    for j in jobs:
        try:
            j["size"] = os.path.getsize(j["log_path"])
        except OSError:
            j["size"] = 0
        del j["log_offset"]
    return jsonify({"jobs": jobs[:100]})


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if not j:
            abort(404)
        j = dict(j)
    try:
        j["size"] = os.path.getsize(j["log_path"])
    except OSError:
        j["size"] = 0
    return jsonify(j)


@app.get("/api/jobs/<job_id>/log")
def api_job_log(job_id):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if not j:
            abort(404)
        path = j["log_path"]
    offset = request.args.get("offset", 0, type=int)
    try:
        size = os.path.getsize(path)
    except OSError:
        return jsonify({"log": "", "offset": 0, "size": 0})
    if offset > size:
        offset = 0
    if offset:
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read(1_000_000)
        return jsonify({"log": chunk.decode(errors="replace"),
                        "offset": offset + len(chunk), "size": size})
    with open(path, "rb") as f:
        chunk = f.read(1_000_000)
    return jsonify({"log": chunk.decode(errors="replace"),
                    "offset": len(chunk), "size": size})


@app.post("/api/jobs/<job_id>/cancel")
def api_job_cancel(job_id):
    return jsonify({"cancelled": cancel_job(job_id)})


@app.get("/")
def index():
    resp = send_from_directory(WEB_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


if __name__ == "__main__":
    from waitress import serve
    print("Ansible Deployer listening on %s:%d" %
          (CFG["listen_host"], CFG["listen_port"]))
    serve(app, host=CFG["listen_host"], port=CFG["listen_port"], threads=8)
