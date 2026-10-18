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


def test_scope_output_refuses_unexecuted_runtime_and_early_browser_success() -> None:
    """Native zero alone cannot earn PASS, and early refusal still names omitted proof."""
    root = Path(__file__).resolve().parents[2]
    probe = r'''
import assert from 'node:assert/strict';
import ScopeReporter from './web/tests/browser/support/scope-reporter.mjs';
import {run} from './web/tests/browser/run.mjs';
import {ownedByProcess} from './web/tests/support/scratch.mjs';
const module = statuses=>({moduleId:'/source/tests/example.test.ts',meta:()=>({}),
 children:{allTests:()=>statuses.map((state,i)=>({fullName:`case ${i}`,result:()=>({state})}))}});
const types = {meta:()=>({typecheck:true}),ok:()=>true};
const reporter = new ScopeReporter();
reporter.onInit({config:{root:'/source',typecheck:{enabled:true}}});
const originalLog=console.log;
const savedReport=process.env.PANELS_COMPANION_REPORT;
delete process.env.PANELS_COMPANION_REPORT;
let output=[];
console.log=(line)=>output.push(line);
const exitCode=process.exitCode;
try {
 for (const modules of [[],[types],[module([]),types],[module(['skipped']),types],
                        [module(['passed','skipped']),types]]) {
  output=[]; reporter.onTestRunEnd(modules,[],'passed');
  assert(output.some(line=>line.includes('PARTIAL INCOMPLETE')));
  assert(!output.some(line=>line.includes('PARTIAL PASS')));
 }
 output=[]; reporter.onTestRunEnd([module(['passed']),types],[],'passed');
 assert(output.some(line=>line.includes('PARTIAL PASS')));
 output=[]; reporter.onTestRunEnd([module(['failed']),types],[],'failed');
 assert(output.some(line=>line.includes('PARTIAL FAIL')));
 assert.equal(process.exitCode,exitCode,'footer must preserve native exit semantics');
 output=[];
 await assert.rejects(run(['artifacts'],{revision:'incorrect'}),
   /Requested browser revision differs from checkout/);
 assert(output.some(line=>line.includes('PARTIAL FAIL')));
 assert(output.some(line=>line.includes('NOT RUN selected cases: file-preview')));
 assert(!output.some(line=>line.includes('PARTIAL PASS')));
 // A different owner's PID can have our PID as a numeric prefix.
 assert(ownedByProcess('host-123.html',123));
 assert(ownedByProcess('cache-123.vite',123));
 assert(!ownedByProcess('host-1234.html',123));
 assert(!ownedByProcess('host-123.html.extra',123));
} finally {
 console.log=originalLog;
 if(savedReport!==undefined)process.env.PANELS_COMPANION_REPORT=savedReport;
}
console.log('Honest scope outcomes and exact scratch ownership passed');
'''
    result = subprocess.run(["node", "--input-type=module", "-e", probe], cwd=root,
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Honest scope outcomes and exact scratch ownership passed" in result.stdout
