#!/usr/bin/env python3
from pathlib import Path
import subprocess
old_art = """  function art(man, label) {
    if (!man || !label) return null;
    var s = slug(label);
    if (man.bySlug[s]) return man.bySlug[s];
    var via = man.byLabel[label.toLowerCase()] || man.byLabel[s.replace(/-/g, " ")];
    return via ? man.bySlug[via] : null;
  }"""
new_art = """  function art(man, label) {
    if (window.__bpMan && window.__bpMan.bySlug) man = window.__bpMan;
    if (!man || !label || !man.bySlug) return null;
    var s = slug(label);
    var rec = man.bySlug[s];
    if (rec && rec.thumb) return rec;
    var via = man.byLabel && (man.byLabel[String(label).toLowerCase()] || man.byLabel[s.replace(/-/g, " ")]);
    if (via && man.bySlug[via] && man.bySlug[via].thumb) return man.bySlug[via];
    var want = s;
    var alias = {"swine":"pigs","rabbits-meat-breeding":"rabbits","bees-apiary":"bees","poultry":"chickens","pets":"dogs","livestock":"cattle"};
    if (alias[want]) want = alias[want];
    var keys = Object.keys(man.bySlug);
    for (var i = 0; i < keys.length; i++) {
      var it = man.bySlug[keys[i]];
      if (it && it.thumb && (it.species === want || keys[i] === want)) return it;
    }
    return null;
  }"""
old_boot = """    fetch("/breeds/manifest.json").then(function (r) { return r.json(); }).then(function (man) {
      MAN = man || MAN;
      boot(); setTimeout(boot, 250); setTimeout(boot, 900);
    }).catch(function () { boot(); });"""
new_boot = """    fetch("/breeds/manifest.json").then(function (r) { return r.json(); }).then(function (man) {
      MAN = man || MAN;
      window.__bpMan = MAN;
      boot(); setTimeout(boot, 250); setTimeout(boot, 900);
      document.querySelectorAll("select[data-bp]").forEach(function (s) {
        s.dispatchEvent(new Event("change", { bubbles: true }));
      });
    }).catch(function () { boot(); });"""
files = [
    Path("/var/www/lostpet/app/wwwroot/js/breed-picker.js"),
    Path("/var/www/lostpet/LostPet_ASPNET/src/LostPet.Web/wwwroot/js/breed-picker.js"),
]
for p in files:
    if not p.exists():
        print("missing", p)
        continue
    t = p.read_text(encoding="utf-8")
    if "window.__bpMan" in t:
        print("already", p)
        continue
    if old_art not in t or old_boot not in t:
        print("UNEXPECTED", p)
        continue
    p.write_text(t.replace(old_art, new_art, 1).replace(old_boot, new_boot, 1), encoding="utf-8")
    print("patched", p)
old, new = b"breed-picker.js?v=2", b"breed-picker.js?v=3"
root = Path("/var/www/lostpet")
for p in root.rglob("*"):
    if not p.is_file():
        continue
    if p.suffix.lower() not in {".dll", ".cshtml", ".html"}:
        continue
    if p.stat().st_size > 80_000_000:
        continue
    try:
        b = p.read_bytes()
    except Exception:
        continue
    if old not in b:
        continue
    p.write_bytes(b.replace(old, new))
    print("version", p)
subprocess.run(["systemctl", "restart", "lostpet"], check=False)
print(subprocess.check_output(["systemctl", "is-active", "lostpet"], text=True).strip())
print("DONE")
