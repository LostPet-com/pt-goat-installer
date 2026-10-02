#!/usr/bin/env python3
"""Read-only. Shows how the live LostPet source compares to git. Changes nothing."""
import os
import subprocess
from pathlib import Path

root = Path("/var/www/lostpet/LostPet_ASPNET")
print("ROOT", root, "exists" if root.is_dir() else "MISSING")

candidates = [root, Path("/var/www/lostpet"), Path("/var/www/lostpet/app")]
git_root = None
for p in candidates:
    if (p / ".git").exists():
        git_root = p
        break
if git_root is None:
    for dirpath, dirs, files in os.walk("/var/www/lostpet"):
        dirs[:] = [d for d in dirs if d not in ("bin", "obj", "runtimes", "publish")]
        if ".git" in dirs:
            git_root = Path(dirpath)
            break

print("GIT_ROOT", git_root if git_root else "NONE")

def run(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    print("----", " ".join(args), "exit", r.returncode)
    out = (r.stdout or "") + (r.stderr or "")
    print(out[:6000] if out else "(empty)")

if git_root:
    run(["git", "status", "-sb"], git_root)
    run(["git", "remote", "-v"], git_root)
    run(["git", "log", "-3", "--oneline"], git_root)
    run(["git", "rev-parse", "--abbrev-ref", "HEAD"], git_root)
    run(["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"], git_root)

print("---- files we will never commit ----")
skip_dirs = {"bin", "obj", "runtimes", ".git", "publish"}
shown = 0
if root.is_dir():
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            fl = f.lower()
            full = os.path.join(dirpath, f)
            risky = (
                fl.endswith((".db", ".db-wal", ".db-shm", ".env"))
                or fl.startswith("appsettings")
                or "smtp" in fl
                or fl.endswith(".bak")
            )
            if risky:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = -1
                print(f"{size:10} {full}")
                shown += 1
                if shown >= 80:
                    print("... more omitted")
                    break
        if shown >= 80:
            break
print("REPORT_DONE")
