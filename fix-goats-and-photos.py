#!/usr/bin/env python3
from pathlib import Path
import subprocess

def patch_dir(p: Path):
    t = p.read_text(encoding="utf-8")
    old = '  if (q.length < 2 && !cityF && !stateF && !zipF) return;'
    new = '  if (q.length < 2 && !cityF && !stateF && !zipF && !speciesFile) return;'
    extra = '''  if (!speciesFile) {
    var n = midName;
    if (n.indexOf("goat") >= 0) speciesFile = "goats";
    else if (n.indexOf("sheep") >= 0) speciesFile = "sheep";
    else if (n.indexOf("pig") >= 0 || n.indexOf("swine") >= 0) speciesFile = "pigs";
    else if (n.indexOf("cattle") >= 0 || n.indexOf("cow") >= 0) speciesFile = "cattle";
    else if (n.indexOf("chicken") >= 0 || n.indexOf("duck") >= 0 || n.indexOf("goose") >= 0 || n.indexOf("turkey") >= 0 || n.indexOf("poultry") >= 0 || n.indexOf("quail") >= 0) speciesFile = "poultry";
    else if (n.indexOf("rabbit") >= 0) speciesFile = "rabbits";
    else if (n.indexOf("bee") >= 0) speciesFile = "bees";
    else if (n.indexOf("camel") >= 0 || n.indexOf("alpaca") >= 0 || n.indexOf("llama") >= 0) speciesFile = "camelids";
    else if (n.indexOf("horse") >= 0) speciesFile = "horses";
    else if (n.indexOf("dog") >= 0) speciesFile = "dogs";
    else if (n.indexOf("cat") >= 0) speciesFile = "cats";
  }
'''
    changed = False
    if old in t:
        t = t.replace(old, new, 1)
        changed = True
    anchor = '  if (!speciesFile && cat === "supplies")'
    if "n.indexOf(\"goat\")" not in t and anchor in t:
        t = t.replace(anchor, extra + anchor, 1)
        changed = True
    if changed:
        p.write_text(t, encoding="utf-8")
        print("dir", p)
    else:
        print("dir-skip", p)

def patch_bp(p: Path):
    t = p.read_text(encoding="utf-8")
    old = 'if (it && it.thumb && (it.species === want || keys[i] === want)) return it;'
    new = 'if (it && it.thumb && (it.species === want || keys[i] === want || (it.species && want.indexOf(it.species) === 0))) return it;'
    if old in t:
        p.write_text(t.replace(old, new, 1), encoding="utf-8")
        print("bp", p)
    else:
        print("bp-skip", p)

root = Path("/var/www/lostpet")
for p in root.rglob("lp-svc-dir.js"):
    patch_dir(p)
for p in root.rglob("breed-picker.js"):
    patch_bp(p)

swaps = [
    ("breed-picker.js?v=2", "breed-picker.js?v=9"),
    ("lp-svc-dir.js?v=3", "lp-svc-dir.js?v=9"),
]
nfiles = 0
for p in root.rglob("*"):
    if not p.is_file():
        continue
    if p.suffix.lower() not in {".dll", ".cshtml", ".html", ".cs"}:
        continue
    if p.stat().st_size > 120_000_000:
        continue
    try:
        b = p.read_bytes()
    except Exception:
        continue
    orig = b
    for a, c in swaps:
        b = b.replace(a.encode(), c.encode())
        b = b.replace(a.encode("utf-16le"), c.encode("utf-16le"))
    if b != orig:
        p.write_bytes(b)
        nfiles += 1
        print("version", p)
print("version-files", nfiles)

marker = "# bp-cachebust"
snip = """
    sub_filter_types text/html;
    sub_filter 'breed-picker.js?v=2' 'breed-picker.js?v=9';
    sub_filter 'lp-svc-dir.js?v=3' 'lp-svc-dir.js?v=9';
    sub_filter_once off;
    # bp-cachebust
"""
touched = []
for p in Path("/etc/nginx").rglob("*"):
    if not p.is_file():
        continue
    if p.suffix not in {".conf", ""} and "conf" not in p.name:
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    if "pettrader.com" not in t or marker in t:
        continue
    lines = t.splitlines(keepends=True)
    out = []
    hit = False
    for line in lines:
        out.append(line)
        if (not hit) and "server_name" in line and "pettrader.com" in line:
            out.append(snip)
            hit = True
    if hit:
        bak = p.with_suffix(p.suffix + ".bpbak")
        bak.write_text(t, encoding="utf-8")
        p.write_text("".join(out), encoding="utf-8")
        touched.append((p, bak))
        print("nginx", p)

test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
print(test.stdout)
print(test.stderr)
if test.returncode != 0:
    for p, bak in touched:
        p.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
        print("reverted", p)
else:
    subprocess.run(["systemctl", "reload", "nginx"], check=False)
    print("nginx-reloaded")

subprocess.run(["systemctl", "restart", "lostpet"], check=False)
print(subprocess.check_output(["systemctl", "is-active", "lostpet"], text=True).strip())
print("DONE")
