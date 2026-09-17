/* Ansible Deployer — Cockpit module frontend.
 * All privileged work (scanning, playbook read, ansible execution) happens in
 * ansible_deployer.py via cockpit.spawn({superuser: "require"}); this file
 * only drives the UI.
 */
"use strict";

(function() {
    const CTRL = "/usr/share/cockpit/ansible-deployer/ansible_deployer.py";
    const CONFIG = "/etc/ansible-deployer/config.json";

    let HOSTS = [];      // [{ip, banner, checked}]
    let PLAYBOOKS = [];  // [{id, path, type, plays, ...}]
    let job = null;      // {id, promise, closed}

    const $ = id => document.getElementById(id);

    /* ------------------------------------------------------------------ */
    /* helpers                                                           */
    /* ------------------------------------------------------------------ */

    function spawnJson(args) {
        const p = cockpit.spawn(["python3", CTRL].concat(args),
                                {superuser: "require"});
        return p.then(out => JSON.parse(out));
    }

    function esc(s) {
        const d = document.createElement("i");
        d.textContent = s;
        return d.innerHTML;
    }

    /* ------------------------------------------------------------------ */
    /* config                                                            */
    /* ------------------------------------------------------------------ */

    cockpit.file(CONFIG, {superuser: "require"}).read()
        .then(text => {
            const c = JSON.parse(text);
            $("ad-subnets").value = (c.scan_subnets || []).join(", ");
            $("ad-cred-user").value = c.default_creds?.user || "agent";
            $("ad-cred-pass").value = c.default_creds?.password || "";
            $("ad-cred-sudo").value = c.default_creds?.sudo_password || "";
            updateCounts();
        })
        .catch(() => { /* no config — user fills in the fields */ });

    /* ------------------------------------------------------------------ */
    /* hosts                                                             */
    /* ------------------------------------------------------------------ */

    $("ad-btn-scan").onclick = async () => {
        const subnets = $("ad-subnets").value.split(",").map(s => s.trim())
            .filter(Boolean);
        if (!subnets.length) return;
        const port = +$("ad-port").value || 22;
        $("ad-btn-scan").disabled = true;
        $("ad-scan-status").innerHTML =
            '<span class="ad-spinner"></span>' +
            cockpit.gettext("scanning $0 …").replace("$0", subnets.join(", "));
        try {
            const hosts = await spawnJson(["scan",
                "--subnets", subnets.join(","), "--port", String(port)]);
            HOSTS = hosts.map(h => ({...h, checked: false}));
            renderHosts();
            $("ad-scan-status").textContent =
                cockpit.gettext("$0 host(s) responding on port $1")
                    .replace("$0", String(hosts.length))
                    .replace("$1", String(port));
        } catch (e) {
            $("ad-scan-status").textContent =
                cockpit.gettext("scan failed: $0").replace("$0", e.message || e);
        }
        $("ad-btn-scan").disabled = false;
    };

    function renderHosts() {
        const tb = $("ad-host-rows");
        tb.innerHTML = "";
        HOSTS.forEach((h, i) => {
            const tr = document.createElement("tr");
            tr.className = "pf-v6-c-table__tr";
            const tdCheck = document.createElement("td");
            tdCheck.className = "pf-v6-c-table__td";
            const check = document.createElement("div");
            check.className = "pf-v6-c-check";
            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.className = "pf-v6-c-check__input";
            cb.onchange = e => { h.checked = e.target.checked;
                tr.classList.toggle("sel", h.checked); updateCounts(); };
            const checkLabel = document.createElement("label");
            checkLabel.className = "pf-v6-c-check__label";
            checkLabel.textContent = "";
            check.append(cb, checkLabel);
            tdCheck.appendChild(check);
            const tdIp = document.createElement("td");
            tdIp.className = "pf-v6-c-table__td ad-ip";
            tdIp.textContent = h.ip;
            const tdBan = document.createElement("td");
            tdBan.className = "pf-v6-c-table__td ad-muted";
            tdBan.textContent = (h.ssh_banner || "").slice(0, 60);
            tr.append(tdCheck, tdIp, tdBan);
            tb.appendChild(tr);
        });
        $("ad-sel-all").checked = false;
        updateCounts();
    }

    $("ad-sel-all").onchange = e => {
        HOSTS.forEach(h => h.checked = e.target.checked);
        renderHosts();
    };

    /* ------------------------------------------------------------------ */
    /* playbooks                                                         */
    /* ------------------------------------------------------------------ */

    async function loadPlaybooks() {
        try {
            PLAYBOOKS = await spawnJson(["playbooks",
                "--dirs", (await configDirs()).join(",")]);
            renderPlaybooks();
        } catch (e) {
            $("ad-pb-hint").textContent =
                cockpit.gettext("failed: $0").replace("$0", e.message || e);
        }
    }

    async function configDirs() {
        try {
            const c = JSON.parse(
                await cockpit.file(CONFIG, {superuser: "require"}).read());
            return c.playbook_dirs || [];
        } catch {
            return ["/opt/ansible-playbooks", "/opt/SHIBA-24"];
        }
    }

    function renderPlaybooks() {
        const q = $("ad-pb-filter").value.toLowerCase();
        const el = $("ad-pb-list");
        el.innerHTML = "";
        let shown = 0;
        PLAYBOOKS.forEach((p, i) => {
            const hay = (p.id + " " + (p.stig_ids || []).join(" ") + " " +
                (p.plays || []).map(x => x.name).join(" ")).toLowerCase();
            if (q && !hay.includes(q)) return;
            shown++;
            const label = document.createElement("label");
            label.className = "ad-play pf-v6-c-radio";
            const radio = document.createElement("input");
            radio.type = "radio";
            radio.name = "ad-pb";
            radio.className = "pf-v6-c-radio__input";
            radio.value = String(i);
            const badge = document.createElement("span");
            badge.className = "ad-badge ad-" + p.type;
            badge.textContent = p.type;
            const name = document.createElement("span");
            name.className = "ad-name";
            const title = p.type === "playbook" && p.plays[0] ? p.plays[0].name : p.id;
            name.textContent = title;
            name.title = p.path;
            const dir = document.createElement("span");
            dir.className = "ad-dir";
            dir.textContent = p.source_dir.replace(/^\/opt\//, "");
            label.append(radio, badge, name, dir);
            el.appendChild(label);
        });
        $("ad-pb-hint").textContent =
            cockpit.gettext("$0 of $1 playbook(s)/task file(s) on this server")
                .replace("$0", String(shown)).replace("$1", String(PLAYBOOKS.length));
    }

    $("ad-pb-filter").oninput = renderPlaybooks;
    $("ad-btn-refresh").onclick = loadPlaybooks;

    /* ------------------------------------------------------------------ */
    /* deploy                                                            */
    /* ------------------------------------------------------------------ */

    function selHosts() { return HOSTS.filter(h => h.checked).map(h => h.ip); }
    function selPb() {
        const r = document.querySelector('input[name="ad-pb"]:checked');
        return r ? PLAYBOOKS[+r.value] : null;
    }

    function updateCounts() {
        const h = selHosts().length, p = selPb() ? 1 : 0;
        $("ad-sel-counts").textContent =
            cockpit.gettext("$0 host(s) · $1 playbook").replace("$0", String(h))
                .replace("$1", String(p));
        const user = $("ad-cred-user").value.trim();
        $("ad-btn-deploy").disabled = !(h && p && user && !job?.active);
    }

    document.body.addEventListener("change", updateCounts);
    document.body.addEventListener("input", e => {
        if (e.target.closest("#ad-cred-user,#ad-cred-port,#ad-cred-pass,#ad-cred-sudo"))
            updateCounts();
    });

    $("ad-btn-deploy").onclick = () => {
        const pb = selPb();
        if (!pb || job?.active) return;
        const args = ["deploy",
            "--hosts", selHosts().join(","),
            "--user", $("ad-cred-user").value.trim(),
            "--port", String(+$("ad-cred-port").value || 22),
            "--target", pb.path];
        const env = [];
        const pass = $("ad-cred-pass").value;
        const sudo = $("ad-cred-sudo").value;
        if (pass) env.push("DEPLOYER_SSH_PASSWORD=" + pass);
        if (sudo) env.push("DEPLOYER_SUDO_PASSWORD=" + sudo);

        const id = "deploy-" + Date.now().toString(36);
        const opts = {superuser: "require"};
        if (env.length) opts.environ = env;
        const p = cockpit.spawn(["python3", CTRL].concat(args), opts);
        p.stream(data => {
            for (const chunk of data)
                appendLog(new TextDecoder().decode(chunk));
            return data.reduce((n, c) => n + c.length, 0);
        });

        job = {id, promise: p, active: true, closed: false};
        $("ad-job-panel").classList.remove("ad-hidden");
        $("ad-job-id").textContent = "#" + id;
        $("ad-job-log").textContent = "";
        $("ad-job-meta").textContent =
            selHosts().join(", ") + " · " + pb.path + " · user " +
            $("ad-cred-user").value.trim() + " · " +
            (pass ? cockpit.gettext("password auth") : cockpit.gettext("ssh-key auth"));
        setJobStatus("running");
        updateCounts();

        p.then(() => finishJob("success"))
         .catch(e => finishJob("error", e));
    };

    function appendLog(text) {
        const el = $("ad-job-log");
        el.textContent += text;
        el.scrollTop = el.scrollHeight;
    }

    function setJobStatus(s, msg) {
        const b = $("ad-job-status");
        b.className = "ad-badge ad-" + s;
        b.textContent = s === "error" ? cockpit.gettext("failed") :
                        s === "cancelled" ? cockpit.gettext("cancelled") :
                        s === "success" ? cockpit.gettext("succeeded") :
                        cockpit.gettext("running");
        if (msg && s === "error") appendLog("\n[deployer] " + msg + "\n");
    }

    function finishJob(status, err) {
        if (!job || job.closed) return;
        job.closed = true;
        job.active = false;
        setJobStatus(status, err?.message);
        updateCounts();
    }

    $("ad-btn-cancel").onclick = () => {
        if (job?.active && !job.closed) {
            job.closed = true;
            job.active = false;
            setJobStatus("cancelled");
            job.promise.close("cancelled");
            updateCounts();
        }
    };

    $("ad-btn-close-job").onclick = () => {
        $("ad-job-panel").classList.add("ad-hidden");
        job = null;
        updateCounts();
    };

    loadPlaybooks();
    updateCounts();
})();
