#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
xcodegen generate --spec project.yml
# XcodeGen 2.46 stringifies nested target attributes (upstream issue #1637).
# Preserve the real Xcode capability dictionary until that fix is released.
python3 - <<'PY'
from pathlib import Path
import json
import subprocess

project = Path('Panels.xcodeproj/project.pbxproj')
data = json.loads(subprocess.check_output(['plutil', '-convert', 'json', '-o', '-', str(project)]))
attributes = data['objects'][data['rootObject']]['attributes']['TargetAttributes']
value = next(item['SystemCapabilities'] for item in attributes.values() if 'SystemCapabilities' in item)
if isinstance(value, str):
    expected = '["com.apple.Push": ["enabled": true]]'
    if value != expected:
        raise SystemExit('Unexpected generated capability value; inspect before changing it')
    source = project.read_text()
    quoted = json.dumps(expected)
    old = 'SystemCapabilities = ' + quoted + ';'
    if source.count(old) != 1:
        raise SystemExit('Expected exactly one generated push capability')
    project.write_text(source.replace(old, 'SystemCapabilities = { com.apple.Push = { enabled = 1; }; };'))
elif value != {'com.apple.Push': {'enabled': True}}:
    raise SystemExit('Unexpected push capability dictionary')
PY
