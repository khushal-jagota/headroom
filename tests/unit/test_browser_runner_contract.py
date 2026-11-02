"""Scope reports must not claim success for checks that never ran."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_scope_output_refuses_unexecuted_runtime_and_early_browser_success(tmp_path: Path) -> None:
    """Native zero alone cannot earn PASS, and early refusal still names omitted proof."""
    root = Path(__file__).resolve().parents[2]
    probe = r'''
import assert from 'node:assert/strict';
import ScopeReporter from './web/tests/browser/support/scope-reporter.mjs';
import {run} from './web/tests/browser/run.mjs';
import {assertCompleteCaseEnvironment} from './web/tests/browser/contracts.mjs';
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
 const selectors=['CONVERSATION_SETTLED_CASE','CONVERSATION_LATEST_CASE',
   'CONVERSATION_SCROLL_CASE','PANELS_CONVERSATION_MENU_ONLY'];
 assertCompleteCaseEnvironment({});
 assertCompleteCaseEnvironment(Object.fromEntries(selectors.map(key=>[key,''])));
 for(const key of selectors) {
  for(const value of ['unread','0',' ']) {
   assert.throws(()=>assertCompleteCaseEnvironment({[key]:value}), error=>
     error.message.includes(`scenario selector: ${key}`));
  }
  process.env[key]='private-value';
  output=[];
  try {
   await assert.rejects(run(['conversation'],{revision:'incorrect'}), error=>
     error.message.includes(`scenario selector: ${key}`) &&
     !error.message.includes('private-value'));
   assert(output.some(line=>line.includes('PARTIAL FAIL')));
   assert(output.some(line=>line.includes('NOT RUN selected cases: conversation-pane')));
   assert(!output.some(line=>line.includes('PARTIAL PASS')));
  } finally { delete process.env[key]; }
 }
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
 await assert.rejects(run(['conversation'],{revision:'incorrect'}),
   /Requested browser revision differs from checkout/);
 assert(output.some(line=>line.includes('PARTIAL FAIL')));
 assert(output.some(line=>line.includes('NOT RUN selected cases: conversation-pane')));
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
console.log('Honest scope managers and exact scratch ownership passed');
'''
    result = subprocess.run(["node", "--input-type=module", "-e", probe], cwd=root,
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Honest scope managers and exact scratch ownership passed" in result.stdout
    selectors = ("CONVERSATION_SETTLED_CASE", "CONVERSATION_LATEST_CASE",
                 "CONVERSATION_SCROLL_CASE", "PANELS_CONVERSATION_MENU_ONLY")
    environment = {key: value for key, value in os.environ.items() if key not in selectors}
    for key in selectors:
        report = tmp_path / f"{key}.json"
        result = subprocess.run(
            ["node", "web/tests/browser/run.mjs", "--report", str(report), "conversation"],
            cwd=root, env=environment | {key: "private-value"}, text=True,
            capture_output=True, check=False,
        )
        assert result.returncode != 0
        assert f"scenario selector: {key}" in result.stderr
        assert "private-value" not in result.stderr
        assert "PARTIAL FAIL" in result.stdout and "PARTIAL PASS" not in result.stdout
        assert not report.exists() or json.loads(report.read_text())["outcome"] != "passed"
