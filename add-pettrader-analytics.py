#!/usr/bin/env python3
import re, subprocess
from pathlib import Path

root = Path("/var/www/lostpet/LostPet_ASPNET/src/LostPet.Web")
pt_dir = root / "Pages/PetTrader"
rx_admin = re.compile(r"""<a\b[^>]*href=(["'])/Admin\1[^>]*>\s*Admin\s*</a>""", re.I)
rx_mine = re.compile(r"<a\b[^>]*>\s*My listings\s*</a>", re.I)
snippet = '@if (User.IsInRole("Admin")) { <a class="btn btn-outline-success btn-sm" href="/Admin/Analytics">Analytics</a> }'
patched = []

if not pt_dir.is_dir():
    raise SystemExit("NO PETTRADER PAGES")

for p in sorted(pt_dir.glob("*.cshtml")):
    t = p.read_text(encoding="utf-8")
    print("---", p.name)
    if "/Admin/Analytics" in t:
        print("already has analytics")
        continue
    m = rx_admin.search(t)
    if m:
        tag = m.group(0)
        new = re.sub(r"""href=(["'])/Admin\1""", 'href="/Admin/Analytics"', tag, count=1, flags=re.I)
        new = re.sub(r">\s*Admin\s*<", ">Analytics<", new, count=1, flags=re.I)
        t = t[:m.end()] + " " + new + t[m.end():]
        p.write_text(t, encoding="utf-8")
        print("cloned admin button")
        patched.append(p.name)
        continue
    m = rx_mine.search(t)
    if m:
        t = t[:m.end()] + "\n      " + snippet + t[m.end():]
        p.write_text(t, encoding="utf-8")
        print("added next to My listings")
        patched.append(p.name)
        continue
    print("no spot")
    for i, line in enumerate(t.splitlines(), 1):
        if re.search(r"Admin|My listings|userbar|Logout", line, re.I):
            print(f"{i}:{line[:240]}")

if not patched:
    raise SystemExit("NOTHING PATCHED")

r = subprocess.run(["dotnet", "publish", "-c", "Release", "-o", "/var/www/lostpet/app"], cwd=root)
print("PUBLISH_EXIT:" + str(r.returncode))
if r.returncode == 0:
    subprocess.run(["systemctl", "restart", "lostpet"])
    subprocess.run(["systemctl", "is-active", "lostpet"])
else:
    print("NOT RESTARTED because publish failed")
