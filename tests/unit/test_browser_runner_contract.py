"""Admission: Node preflight must refuse incomplete proof before browser setup.

The control-owned Python report tests cannot prove this earlier Node boundary.
Reuse the real contract and spawn helper. No build, server, or browser is needed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def test_node_companion_refuses_missing_spawn_and_incomplete_results() -> None:
    root = Path(__file__).resolve().parents[2]
    probe = r'''
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {validateCompanion} from './web/tests/browser/contracts.mjs';
import {runCommand} from './web/tests/browser/run.mjs';
const file = 'web/tests/workspace-rail-projection.test.ts';
const valid = {schema:1,invocationId:'one',revision:'a'.repeat(40),outcome:'passed',
 compiler:'passed',runtime:[{path:file,cases:[{name:'Node without DOM',status:'passed'}]}]};
validateCompanion(valid,'one','a'.repeat(40),[file]);
for (const mutate of [
 v=>v.runtime=[],
 v=>v.runtime[0].cases=[],
 v=>v.runtime[0].cases[0].status='skipped',
 v=>v.runtime[0].cases[0].status='failed',
 v=>v.runtime[0].path='other.test.ts',
 v=>v.runtime.push(v.runtime[0]),
 v=>v.compiler='not-passed',
 v=>v.invocationId='another',
 v=>v.revision='b'.repeat(40),
]) {
 const value = structuredClone(valid); mutate(value);
 assert.throws(()=>validateCompanion(value,'one','a'.repeat(40),[file]));
}
assert.throws(()=>validateCompanion({},'one','a'.repeat(40),[file]));
await assert.rejects(readFile('/nonexistent-panels-companion-report'),{code:'ENOENT'});
await assert.rejects(runCommand('/nonexistent-panels-companion-tool',[]),{code:'ENOENT'});
console.log('Node companion preflight refusal cases passed');
'''
    result = subprocess.run(["node", "--input-type=module", "-e", probe], cwd=root,
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Node companion preflight refusal cases passed" in result.stdout
