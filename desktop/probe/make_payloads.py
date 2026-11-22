"""PROBE ONLY. Build screens payloads A, B, C and R for the screens probe.

Layout served: <out>/screens/<name>/manifest.json and <out>/screens/<name>/files/<path>.
The payload digest is SHA256 over sorted "<file sha256>  <path>\n" lines.
"""
import hashlib, json, os, shutil, sys

out, packed = sys.argv[1], sys.argv[2]

def page(name, color, script):
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Screens {name}</title>
<link rel="stylesheet" href="/_app/style.css"></head>
<body style="background:{color};color:#fff;font:48px -apple-system">
<h1>Screens payload {name}</h1><p id="t">served from the data folder</p>
<script src="{script}"></script></body></html>"""

def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)

def manifest(name):
    root = os.path.join(out, "screens", name, "files")
    files = []
    for d, _, names in os.walk(root):
        for n in names:
            full = os.path.join(d, n)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files.append({"path": rel, "sha256": hashlib.sha256(open(full, "rb").read()).hexdigest()})
    files.sort(key=lambda f: f["path"])
    lines = "".join(f"{f['sha256']}  {f['path']}\n" for f in files)
    m = {"name": name, "digest": hashlib.sha256(lines.encode()).hexdigest(), "files": files}
    with open(os.path.join(out, "screens", name, "manifest.json"), "w") as f:
        json.dump(m, f, indent=1)
    print(name, m["digest"], len(files), "files")

for name, color in [("A", "#1d6b3a"), ("B", "#7a2a8c"), ("C", "#8c2a2a")]:
    root = os.path.join(out, "screens", name, "files")
    script = "/_app/marker.js" if name != "C" else "/_app/missing.js"
    write(root, "web/dist/index.html", page(name, color, script))
    write(root, "web/dist/style.css", "h1{letter-spacing:2px}")
    if name != "C":
        write(root, "web/dist/marker.js", f"window.__screens = {{ version: {json.dumps(name)} }};")
    manifest(name)

r = os.path.join(out, "screens", "R", "files")
for part in ("web", "assets", "static"):
    shutil.copytree(os.path.join(packed, part), os.path.join(r, part))
manifest("R")
