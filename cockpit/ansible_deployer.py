#!/usr/bin/env python3
"""
ansible_deployer.py — root controller for the Cockpit "Ansible Deployer" module.

Invoked from the module frontend via cockpit.spawn(); subcommands:

  scan       --subnets CIDR[,CIDR] [--port N] [--timeout S]
             concurrent SSH-banner scan, prints JSON array [{ip, banner}]

  playbooks  --dirs DIR[,DIR]
             read-only walk + classify of playbooks/tasks, prints JSON array

  check      (no args)
             prints JSON {uid, user, key_ok, key, key_problem} — whether the
             running user can read the configured deploy_key. Frontend uses
             this to show an access banner for users without deploy rights.

  deploy     --hosts IP[,IP] --user USER [--port N] --target PB[,PB...]
             [--key PATH]
             Password (if any) is read from env DEPLOYER_SSH_PASSWORD,
             sudo password from env DEPLOYER_SUDO_PASSWORD (never argv).
             Writes a per-deployment inventory, runs `ansible -m ping`
             preflight, then `ansible-playbook` for EACH target, in the
             given order (bare task files are auto-wrapped in a writable
             per-job temp dir). All ansible output streams to stdout.
             A failing playbook is reported but the remaining targets
             still run; exits 0 only if every target succeeded.

Note: the module runs as the LOGGED-IN Cockpit user (superuser:false), not
root. It therefore reads /etc/ansible-deployer/config.json, the playbook dirs,
and the deploy_key as that user — all of which must be world/group readable.
Target-side privilege (become/sudo) still uses the collected sudo password.
"""
import argparse
import concurrent.futures
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

CONFIG_PATH = os.environ.get("ANSIBLE_DEPLOYER_CONFIG",
                             "/etc/ansible-deployer/config.json")

DEFAULTS = {
    "scan_subnets": ["10.10.10.0/24"],
    "scan_port": 22,
    "scan_timeout_s": 2.5,
    "playbook_dirs": ["/opt/ansible-playbooks", "/opt/SHIBA-24"],
    "default_creds": {"user": "agent", "password": "", "sudo_password": "",
                      "port": 22},
    # Path to the SSH key the deployer authenticates to targets with. Must be
    # readable by the Cockpit user running the module (i.e. NOT /root/... which
    # a non-root login cannot read). See install.sh (deployer group + key).
    "deploy_key": "",
}


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except OSError:
        return DEFAULTS
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update(cfg)
    merged.setdefault("default_creds", DEFAULTS["default_creds"])
    return merged


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def ssh_banner(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            return s.recv(256).decode(errors="replace").strip() or None
    except OSError:
        return None


def cmd_scan(args):
    results = []
    for net in args.subnets:
        try:
            net_obj = ipaddress.ip_network(net, strict=False)
            hosts = [str(h) for h in net_obj.hosts() if h.version == 4]
        except ValueError:
            continue
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(hosts), 256)) as pool:
            futs = {pool.submit(ssh_banner, h, args.port, args.timeout): h
                    for h in hosts}
            for fut in concurrent.futures.as_completed(futs):
                h = futs[fut]
                banner = fut.result()
                if banner:
                    results.append({"ip": h, "ssh_banner": banner})
    results.sort(key=lambda r: [int(x) for x in r["ip"].split(".")])
    print(json.dumps(results))
    return 0


# ---------------------------------------------------------------------------
# playbooks
# ---------------------------------------------------------------------------

import yaml  # noqa: E402


def summarize_playbook(path):
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return {"id": os.path.basename(path), "path": path, "source_dir": "error",
                "type": "unknown", "plays": [], "stig_ids": [], "needs_wrap": False}

    source_dir = os.path.dirname(path)
    plays = []
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None

    if isinstance(parsed, list) and parsed:
        if any(isinstance(p, dict) and "hosts" in p for p in parsed):
            for item in parsed:
                if isinstance(item, dict):
                    tags = item.get("tags") or []
                    plays.append({"id": tags[-1] if tags else "play",
                                  "name": str(item.get("name", "play")),
                                  "hosts": str(item.get("hosts", "")),
                                  "tags": tags})
        elif all(isinstance(p, dict) and "name" in p for p in parsed):
            for item in parsed:
                tags = item.get("tags") or []
                plays.append({"id": tags[-1] if tags else "task",
                              "name": str(item["name"]), "hosts": "", "tags": tags})

    if not plays:
        m = re.search(r"STIG ID:\s*(\S+)", text)
        if m:
            plays.append({"id": m.group(1), "name": m.group(1), "hosts": "",
                          "tags": []})

    ptype = ("playbook" if any(p["hosts"] for p in plays)
             else ("task" if plays else "other"))
    stigs = sorted(set(re.findall(r"UBTU[-_]24[-_]?\d*", text)))[:50]
    # True if this file requests privilege escalation (become: yes/true).
    # The frontend uses this to warn users that a sudo password is required
    # (or a NOPASSWD sudoers rule on the target), since a missing become
    # password surfaces as the cryptic Ansible error "Missing sudo password".
    needs_become = bool(re.search(r"^\s*become\s*:\s*(true|yes)\s*$", text,
                                  re.MULTILINE | re.IGNORECASE))
    return {"id": os.path.basename(path), "path": path, "source_dir": source_dir,
            "type": ptype, "plays": plays, "stig_ids": stigs,
            "needs_wrap": ptype == "task", "needs_become": needs_become}


def cmd_playbooks(args):
    out = []
    for base in args.dirs:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in sorted(files):
                if fn.endswith((".yml", ".yaml")) and not fn.startswith("__deployer_job_"):
                    out.append(summarize_playbook(os.path.join(root, fn)))
    out.sort(key=lambda p: (p["source_dir"], p["id"]))
    print(json.dumps(out))
    return 0


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------

def stream_proc(cmd, env, timeout=None):
    """Run cmd, pipe stdout/stderr straight to our stdout. Returns exit code."""
    p = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for line in iter(p.stdout.readline, b""):
            sys.stdout.write(line.decode(errors="replace"))
            sys.stdout.flush()
        p.stdout.close()
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        rc = 124
    return rc


def cmd_deploy(args):
    cfg = load_config()
    user = args.user
    port = args.port or 22
    targets = [t for t in (x.strip() for x in args.target.split(",")) if t]
    if not targets:
        sys.stderr.write("no --target given\n")
        return 2
    missing = [t for t in targets if not os.path.exists(t)]
    if missing:
        sys.stderr.write("target(s) do not exist:\n"
                         + "".join("  %s\n" % t for t in missing))
        return 2

    password = os.environ.get("DEPLOYER_SSH_PASSWORD", "")
    sudo_password = os.environ.get("DEPLOYER_SUDO_PASSWORD", "")

    env = dict(os.environ,
               ANSIBLE_HOST_KEY_CHECKING="False",
               ANSIBLE_DEPRECATION_WARNINGS="False")
    ssh_args = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    # Key resolution order: --key flag, config deploy_key, then ~/.ssh/id_ed25519.
    # The module runs as the logged-in Cockpit user (not root), so the default
    # /root key is unreadable — config.json's deploy_key is the real path.
    key = args.key or (cfg.get("deploy_key") or "").strip() \
        or os.path.expanduser("~/.ssh/id_ed25519")
    # The deploy key is ALWAYS offered when one is configured and readable,
    # even when an SSH password was also entered. Reason: this host has no
    # sshpass, so a password-only SSH session can never complete — with the
    # key offered, ssh authenticates by key instantly and the password field
    # is simply unused for the transport (it is NOT the sudo/become
    # credential; that is DEPLOYER_SUDO_PASSWORD -> ANSIBLE_BECOME_PASS).
    # Omitting the key in password mode caused a 30s preflight timeout and
    # "UNREACHABLE ... authentication failed" on every job.
    if os.path.exists(key):
        ssh_args += " -i %s" % key
    env["ANSIBLE_SSH_ARGS"] = ssh_args
    if password:
        env["ANSIBLE_SSH_PASS"] = password
    # Become/sudo password: the Sudo field wins. If it is empty but an SSH
    # password was entered, REUSE the SSH password for become — equivalent
    # to what `ansible -K` would collect interactively, but delivered to a
    # non-interactive spawned process (there is no TTY to answer -K with).
    # This is what makes "same password in both fields, nothing in Sudo"
    # work on targets where the user's login password is also their sudo
    # password — the common lab/STIG-test setup.
    if sudo_password:
        env["ANSIBLE_BECOME_PASS"] = sudo_password
    elif password:
        env["ANSIBLE_BECOME_PASS"] = password

    inv_dir = tempfile.mktemp(prefix="ansible-deployer-", dir="/var/tmp")
    os.makedirs(inv_dir)
    inventory_path = os.path.join(inv_dir, "inventory.ini")
    hosts = sorted(set(args.hosts.split(",")))
    with open(inventory_path, "w") as f:
        f.write("[targets]\n")
        for h in hosts:
            f.write("%s ansible_port=%s ansible_user=%s\n" % (h, port, user))

    print("[deployer] targets: %s (user=%s port=%d auth=%s)\n"
          % (", ".join(hosts), user, port,
             "password" if password else "ssh-key"))

    # bare task file -> thin wrapper playbook, per target.
    #
    # The wrapper is written to the PER-JOB temp dir (world-creatable /var/tmp),
    # NOT next to the task file. Task directories such as /opt/SHIBA-24 are
    # root-owned, so a non-root Cockpit user cannot create files there. Because
    # the wrapper lives outside the task's directory, the include_tasks path must
    # be an ABSOLUTE path to the real task file (relative includes resolve
    # against the wrapper's own directory).
    def wrap_if_needed(target, idx):
        info = summarize_playbook(target)
        if not info["needs_wrap"]:
            return target
        tname = os.path.basename(target)
        wrapper = os.path.join(inv_dir, "__deployer_job_%d_%d.yml"
                               % (os.getpid(), idx))
        with open(wrapper, "w") as f:
            f.write("# generated by ansible-deployer cockpit module — safe to delete\n")
            f.write("- name: Deployer job (wrapper)\n  hosts: all\n")
            # Only escalate when the included task actually asks for
            # privilege — otherwise a task that needs no root would still
            # fail with "Missing sudo password" because of the wrapper.
            if info["needs_become"]:
                f.write("  become: yes\n")
            f.write("  tasks:\n    - name: Include STIG task %s\n" % tname)
            f.write("      ansible.builtin.include_tasks: %s\n" % os.path.abspath(target))
        print("[deployer] task file detected — wrapped as %s (includes %s%s)\n"
              % (wrapper, os.path.abspath(target),
                 ", become" if info["needs_become"] else ""))
        return wrapper

    # preflight (once, against the shared inventory)
    print("[deployer] preflight: ansible -m ping")
    rc = stream_proc(["ansible", "-i", inventory_path, "all", "-m", "ping", "-o"],
                     env, timeout=300)
    if rc != 0:
        print("\n[deployer] preflight FAILED — connectivity check did not "
              "succeed. Aborting before running any playbook.")
        return rc

    # main run: each target in the order the user picked it; a failure is
    # reported but the remaining targets still run, so one broken task file
    # does not mask the rest of a batch.
    results = []
    n = len(targets)
    for idx, target in enumerate(targets, 1):
        run_target = wrap_if_needed(target, idx)
        print("[deployer] (%d/%d) running: ansible-playbook %s\n"
              % (idx, n, run_target))
        rc = stream_proc(["ansible-playbook", "-i", inventory_path, run_target],
                         env)
        print("\n[deployer] (%d/%d) %s — exit code: %d\n"
              % (idx, n, os.path.basename(target), rc))
        results.append((os.path.basename(target), rc))

    ok = sum(1 for _t, rc in results if rc == 0)
    print("[deployer] summary: %d/%d playbook(s) succeeded" % (ok, n))
    for name, rc in results:
        if rc != 0:
            print("[deployer]   failed: %s (exit %d)" % (name, rc))
    return 0 if ok == n else 1


# ---------------------------------------------------------------------------
# check — access gate for the frontend
# ---------------------------------------------------------------------------

def cmd_check(args):
    """Report whether the user running the module can actually use it.

    The module runs as the logged-in Cockpit user (superuser:false), so the
    two things that make a deploy work are:
      * the deploy SSH key exists AND is readable by that user
      * (informational) the user is a member of the configured group
    The frontend shows a banner when access is missing, so a non-admin who is
    not in the deployer group sees *why* Deploy is disabled instead of a
    cryptic ssh failure.
    """
    cfg = load_config()
    key = (cfg.get("deploy_key") or "").strip()
    key_ok = False
    key_problem = ""
    if not key:
        key_problem = "no deploy key configured in /etc/ansible-deployer/config.json"
    elif not os.path.exists(key):
        key_problem = "deploy key not found: %s" % key
    elif not os.access(key, os.R_OK):
        key_problem = "deploy key exists but is not readable by you: %s" % key
    else:
        key_ok = True

    print(json.dumps({
        "uid": os.getuid(),
        "user": os.environ.get("USER", os.environ.get("USERNAME", "")),
        "key_ok": key_ok,
        "key": key,
        "key_problem": key_problem,
    }))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--subnets", required=True)
    p.add_argument("--port", type=int, default=22)
    p.add_argument("--timeout", type=float, default=2.5)
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("playbooks")
    p.add_argument("--dirs", required=True)
    p.set_defaults(fn=cmd_playbooks)

    p = sub.add_parser("check")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("deploy")
    p.add_argument("--hosts", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--target", required=True)
    p.add_argument("--key", default="")
    p.set_defaults(fn=cmd_deploy)

    args = ap.parse_args()
    if args.cmd == "scan":
        args.subnets = [s.strip() for s in args.subnets.split(",") if s.strip()]
    if args.cmd == "playbooks":
        args.dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
