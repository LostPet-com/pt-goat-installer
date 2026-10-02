#!/usr/bin/env python3
"""Commit and push the live LostPet source. Does not restart the site.
Skips backups, the database, uploads, and mail settings.
"""
import os
import subprocess
from pathlib import Path

root = Path("/var/www/lostpet/LostPet_ASPNET")
os.chdir(root)
env = os.environ.copy()
env["GIT_TERMINAL_PROMPT"] = "0"

def run(args, check=False):
    print("----", " ".join(args))
    r = subprocess.run(args, cwd=root, env=env, text=True)
    print("exit", r.returncode)
    if check and r.returncode != 0:
        raise SystemExit(r.returncode)
    return r.returncode

gi = root / ".gitignore"
text = gi.read_text(encoding="utf-8") if gi.exists() else ""
if "*.bak" not in text:
    gi.write_text(text.rstrip() + "\n\n# local backups, not source\n*.bak\n*.bak-*\n")
    print("gitignore updated")
else:
    print("gitignore already ignores bak files")

email = subprocess.run(["git", "config", "user.email"], cwd=root, capture_output=True, text=True)
if email.returncode != 0 or not email.stdout.strip():
    subprocess.run(["git", "config", "user.email", "deploy@lostpet.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "LostPet"], cwd=root, check=True)
    print("set local git identity")
else:
    print("git identity", email.stdout.strip())

run(["git", "add", "-A"])

names = subprocess.run(
    ["git", "diff", "--cached", "--name-only"],
    cwd=root, capture_output=True, text=True, check=True,
).stdout.splitlines()
names = [n for n in names if n.strip()]
bad = [
    n for n in names
    if ".bak" in n or n.endswith(".db") or n.endswith(".env")
    or "uploads/" in n or n.endswith("appsettings.Production.json")
    or "smtp.env" in n
]
if bad:
    print("REFUSING these paths:")
    for n in bad:
        print(" ", n)
    raise SystemExit(2)

print("STAGED", len(names))
for n in names:
    print(" ", n)

if not names:
    print("NOTHING TO COMMIT")
    raise SystemExit(0)

run([
    "git", "commit", "-m",
    "Save live LostPet source before the PetTrader split.\n\nDatabase, mail password, uploads, and backup files stay on the server.",
], check=True)
code = run(["git", "push", "origin", "main"])
print("PUSH_EXIT:" + str(code))
run(["git", "status", "-sb"])
print("SAVE_DONE")
