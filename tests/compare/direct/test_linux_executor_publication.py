"""The executor receipt binds native archive bytes and a successful build to its source."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from scripts.update_publication import validate_linux_executor_publication
from tests.compare.direct.test_unit21_updates import ROOT, _environment, _report
from tests.support.update_apps import build_app, rewrite_artifact_digest

from planner.environments.runtime_contracts import NodeRuntimeContract


def test_linux_executor_publication_matches_selected_source(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Gap: the native receipt must refuse failed builds and changed proof, role or bytes."""
    started = time.perf_counter()
    revision = "a" * 40
    app = build_app(tmp_path / "payload", revision, "sentinel")
    shutil.rmtree(app / ".venv")
    entrypoint = app / "server/executor/mac/entrypoint.ts"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// synthetic native entrypoint\n")
    marker = dict(
        schema_version=1,
        application_revision=revision,
        platform="linux",
        architecture="x86_64",
        role="executor",
    )
    (app / "mac-runtime.json").write_text(json.dumps(marker))
    contract = NodeRuntimeContract(
        hashlib.sha256((app / "runtime/inventory.json").read_bytes()).hexdigest()
    ).as_dict()
    (app / "runtime/contract.json").write_text(json.dumps(contract))
    manifest = json.loads((app / "manifest.json").read_text())
    (app / "manifest.json").write_text(json.dumps({**manifest, "runtime_contract": contract}))
    rewrite_artifact_digest(app)
    archive = tmp_path / "panels-linux-executor.tar.gz"
    with tarfile.open(archive, "w:gz") as packed:
        for child in app.iterdir():
            packed.add(child, arcname=child.name)
    original = archive.read_bytes()
    receipt = dict(
        marker,
        archive_sha256=hashlib.sha256(original).hexdigest(),
        archive_size=len(original),
        proof=dict(native_startup="passed", relocated_archive="passed", executor_cli="passed"),
    )
    path = tmp_path / "linux-executor-publication.json"
    cases: list[dict[str, Any]] = [
        {},
        {"build_result": "failure"},
        {"patch": {"role": "home"}},
        {"patch": {"proof": {}}},
        {"changed": True},
    ]
    expected = []
    for case in cases:
        path.write_text(json.dumps({**receipt, **case.get("patch", {})}))
        archive.write_bytes(original + (b"changed" if case.get("changed") else b""))
        try:
            result = validate_linux_executor_publication(
                tmp_path,
                requested=revision,
                artifact_id=41,
                build_result=case.get("build_result", "success"),
            )
            expected.append(asdict(result))
        except ValueError as error:
            expected.append({"error": str(error)})
    archive.write_bytes(original)
    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps(dict(root=str(tmp_path), receipt=receipt, cases=cases, revision=revision))
    )
    code = """
import {readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {ChildProcesses} from './server/lifecycle/owned_processes.ts';
const {validate_linux_executor_publication}=await import(process.argv[1]);
const input=JSON.parse(readFileSync(process.argv[2],'utf8'));
const owner=new ChildProcesses(), controller=new AbortController();
const execution={child_processes:owner,signal:controller.signal,deadline:performance.now()/1000+15};
const archive=join(input.root,'panels-linux-executor.tar.gz'), original=readFileSync(archive);
const result=[];
try {
  for(const item of input.cases){
    writeFileSync(join(input.root,'linux-executor-publication.json'),
      JSON.stringify({...input.receipt,...item.patch}));
    writeFileSync(archive,item.changed?Buffer.concat([original,Buffer.from('changed')]):original);
    try {result.push(await validate_linux_executor_publication(input.root,input.revision,41,
      item.build_result??'success',execution));} catch(error){result.push({error:error.message});}
  }
  console.log(JSON.stringify(result));
} finally {controller.abort();owner.seal();await owner.settled();}
"""
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            code,
            (ROOT / "server/environments/release/publication_receipt.ts").as_uri(),
            str(input_path),
        ],
        cwd=ROOT,
        env=_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    _report(
        request,
        "linux-executor-publication.json",
        dict(
            equal=actual == expected,
            python=expected,
            typescript=actual,
            seconds=time.perf_counter() - started,
        ),
    )
    assert actual == expected
