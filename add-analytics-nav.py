#!/usr/bin/env python3
import re, subprocess
from pathlib import Path

root = Path("/var/www/lostpet/LostPet_ASPNET/src/LostPet.Web")
rx = re.compile(r"<a\b[^>]*>\s*Cleanup\s*</a>", re.I)
patched = []

for p in list(root.rglob("*.cshtml")) + list(root.rglob("*.cs")):
    t = p.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"cleanup", t, re.I):
        continue
    print("---", p)
    for i, line in enumerate(t.splitlines(), 1):
        if re.search(r"cleanup", line, re.I):
            print(f"{i}:{line[:400]}")
    if "/Admin/Analytics" in t:
        print("already linked")
        continue

    def repl(m):
        tag = m.group(0)
        if re.search(r"\bhref\s*=", tag, re.I):
            new = re.sub(r"href\s*=\s*(['\"])[^'\"]*\1", 'href="/Admin/Analytics"', tag, count=1, flags=re.I)
        elif re.search(r"\basp-page\s*=", tag, re.I):
            new = re.sub(r"asp-page\s*=\s*(['\"])[^'\"]*\1", 'href="/Admin/Analytics"', tag, count=1, flags=re.I)
        else:
            new = tag.replace("<a", '<a href="/Admin/Analytics"', 1)
        new = re.sub(r">\s*Cleanup\s*<", ">Analytics<", new, count=1, flags=re.I)
        return tag + " " + new

    new_t, n = rx.subn(repl, t, count=1)
    if n == 0:
        print("NO TAG PATCH")
        continue
    bak = Path(str(p) + ".bak-analytics-link")
    if not bak.exists():
        bak.write_text(t, encoding="utf-8")
    p.write_text(new_t, encoding="utf-8")
    patched.append(str(p))
    print("patched", p)

if not patched:
    raise SystemExit("NOTHING PATCHED")

r = subprocess.run(["dotnet", "publish", "-c", "Release", "-o", "/var/www/lostpet/app"], cwd=root)
print("PUBLISH_EXIT:" + str(r.returncode))
if r.returncode == 0:
    subprocess.run(["systemctl", "restart", "lostpet"])
    subprocess.run(["systemctl", "is-active", "lostpet"])
else:
    print("NOT RESTARTED because publish failed")
