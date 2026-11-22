import json, glob, os, sys
for p in sorted(glob.glob(os.path.join(sys.argv[1], "probe-report-*.json"))):
    d = json.load(open(p))
    print(os.path.basename(p), "electron", d["electron"], "atStart", d["atStart"])
    for s in d["steps"]:
        print("  ", "PASS" if s["ok"] else "FAIL", s["step"], "| marker", s.get("marker"), "| errors", len(s.get("consoleErrors") or []), "| q", s.get("quarantine", ""), (s.get("error") or "")[:400])
