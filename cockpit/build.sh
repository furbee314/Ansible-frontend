#!/usr/bin/env bash
# Build the Ansible Deployer Cockpit module's frontend assets.
#
# A Cockpit module is served inside its own iframe, so it does NOT inherit the
# shell's PatternFly CSS. This vendored build copies the prebuilt PatternFly
# v6 stylesheet (components + dark theme) into the module directory and then
# sanity-checks that every PF class the HTML/JS reference actually exists in
# that bundle.
#
#   npm install          # one-time, fetches @patternfly/patternfly
#   ./build.sh           # vendor patternfly.css + validate classes
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PF="$HERE/node_modules/@patternfly/patternfly/patternfly.css"

[ -f "$PF" ] || { echo "patternfly.css not found; run: npm install" >&2; exit 1; }
cp "$PF" "$HERE/patternfly.css"
echo "vendored patternfly.css ($(du -h "$HERE/patternfly.css" | cut -f1))"

# Validate that every PF class referenced in the module exists in the bundle.
python3 - "$HERE" <<'PY'
import re, sys, glob
d = sys.argv[1]
css = open(d + "/patternfly.css").read()
refs = set()
for f in glob.glob(d + "/index.html") + glob.glob(d + "/ansible-deployer.js"):
    txt = open(f).read()
    refs |= set(re.findall(r'pf-v6-(?:c|l|u|m|t)-[a-z0-9-]+', txt))
missing = [r for r in sorted(refs) if ("." + r) not in css and r not in css]
if missing:
    print("WARNING: these PF classes are referenced but not in patternfly.css:")
    for m in missing:
        print("   -", m)
    sys.exit(0)   # warn, don't fail — utilities etc. may be intentionally ours
print(f"OK: all {len(refs)} referenced PF classes present in patternfly.css")
PY
