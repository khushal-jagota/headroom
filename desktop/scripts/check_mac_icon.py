"""Check the real signed bundle and export its icon beside two native Mac icons."""
import hashlib
import html
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time

app, expected_icon, output = map(Path, sys.argv[1:])
output.mkdir(parents=True, exist_ok=True)


def info(bundle):
    with (bundle / 'Contents/Info.plist').open('rb') as source:
        return plistlib.load(source)


def icon(bundle):
    name = info(bundle)['CFBundleIconFile']
    if not name.endswith('.icns'):
        name += '.icns'
    path = bundle / 'Contents/Resources' / name
    assert path.is_file(), f'Missing bundle icon: {path}'
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


metadata = info(app)
for key in ['CFBundleName', 'CFBundleDisplayName', 'CFBundleExecutable']:
    assert metadata[key] == 'Panels', (key, metadata[key])
assert metadata['CFBundleIdentifier'] == 'com.khushaljagota.panels.desktop'
bundle_icon = icon(app)
assert sha256(bundle_icon) == sha256(expected_icon), 'Signed bundle icon differs from committed icon'

# Launch only this app, with private state, and stop that exact process afterward.
with tempfile.TemporaryDirectory(prefix='panels-icon-') as state:
    with (output / 'launch.log').open('w') as log:
        process = subprocess.Popen(
            [str(app / 'Contents/MacOS/Panels')], stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, 'PANELS_DESKTOP_USER_DATA': state},
        )
        try:
            time.sleep(3)
            assert process.poll() is None, 'Panels exited before the process name check'
            executable = subprocess.check_output(
                ['ps', '-p', str(process.pid), '-o', 'comm='], text=True,
            ).strip()
            assert Path(executable).name == 'Panels', executable
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

report = {
    'bundle': str(app),
    'identity': {key: metadata[key] for key in [
        'CFBundleName', 'CFBundleDisplayName', 'CFBundleExecutable', 'CFBundleIdentifier', 'CFBundleIconFile',
    ]},
    'iconSha256': sha256(bundle_icon),
    'processExecutable': executable,
    'scope': 'Bundle identity and real process check. About and notifications use this native identity; no GUI capture of those surfaces.',
}
(output / 'identity.json').write_text(json.dumps(report, indent=2) + '\n')

sources = {
    'Panels': bundle_icon,
    'Finder': icon(Path('/System/Library/CoreServices/Finder.app')),
    'TextEdit': icon(Path('/System/Applications/TextEdit.app')),
}
sizes = [16, 32, 128, 512, 1024]
for name, source in sources.items():
    full = output / f'{name}.png'
    subprocess.run(['sips', '-s', 'format', 'png', str(source), '--out', str(full)], check=True)
    for size in sizes:
        subprocess.run(['sips', '-z', str(size), str(size), str(full), '--out', str(output / f'{name}-{size}.png')], check=True)

rows = []
for size in sizes:
    images = ''.join(f'<td><img src="{name}-{size}.png" width="{size}" height="{size}" alt="{name} at {size}px"></td>' for name in sources)
    rows.append(f'<tr><th>{size}px</th>{images}</tr>')
(output / 'index.html').write_text('''<!doctype html><meta charset="utf-8"><title>Panels signed icon comparison</title>
<style>body{font:16px system-ui;background:#25272a;color:#eee;padding:24px}table{border-collapse:collapse}td,th{padding:16px;text-align:center;border-bottom:1px solid #555}img{display:block}pre{white-space:pre-wrap}</style>
<h1>Panels signed icon comparison</h1><p>Panels comes from the signed app ZIP. Finder and TextEdit come from this hosted Mac.</p>
<p>Each image uses its stated pixel dimensions. The smaller images show Dock scale; the full image shows margins and shadow.</p>
<table><thead><tr><th>Size</th><th>Panels</th><th>Finder</th><th>TextEdit</th></tr></thead><tbody>'''
    + ''.join(rows) + '</tbody></table><h2>Verified identity</h2><pre>' + html.escape(json.dumps(report, indent=2)) + '</pre>')
print(json.dumps(report, indent=2))
