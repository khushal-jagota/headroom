"""Retained dataset rows cross the real CLI and HTTP boundary in both output modes.

The route-list seed cannot publish dataset results. Reuse the existing canonical
source/dataset setup and lifecycle writers before the fixture-owned host starts.
No provider, script runtime, additional host type or raw seed SQL enters this proof.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.unit20_cli import ROOT, CliHost, comparable, run_cli
from tests.compare.runner import Normalizer, backup_database, substitute
from tests.support.measure_sources import NOW, configured_source
from tests.support.server import ServerHandle
from tests.support.server_fixtures import _start_server, _stop_server
from tests.unit.test_dataset_persistence import dataset

from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.datasets.contracts import CandidateDatasetResult, DatasetRow
from planner.measure_sources import run_actions
from planner.measure_sources.contracts import RunMetrics, RunMode, RunResult, RunStatus

LIST = ROOT / "tests/compare/lists/unit-20-commands.domain.json"


def test_unit20_retained_dataset_cli(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    captures: dict[str, Any] = {}

    def seed(target: Path) -> None:
        # _start_server scopes synthetic provider homes around this callback and
        # creates the schema before calling it. No server process owns this DB yet.
        with closing(connect(str(target))) as conn:
            source, _ = configured_source(conn, enabled=False)
            item = dataset(conn, source.id)
            assert source.active_script is not None
            admitted = run_actions.request_run(
                conn,
                source.id,
                mode=RunMode.collection,
                history=None,
                expected_script_revision=source.active_script.revision,
                principal=OWNER_PRINCIPAL,
                now=NOW,
            )
            claimed = run_actions.claim_run(conn, admitted.id, now=NOW)
            assert claimed is not None
            candidate = CandidateDatasetResult(
                item.id,
                "2026-10-26",
                (DatasetRow({"region": "de", "device": "web"}, {"events": 7.25}),),
                float(NOW),
                query_start_at=float(NOW - 86400),
                labels={"region": {"de": "Deutschland"}},
            )
            completed = run_actions.complete_run(
                conn,
                admitted.id,
                RunResult((), None, RunMetrics(0, 0, 0, 1, 0), (candidate,)),
                now=NOW,
            )
            assert completed.run.status is RunStatus.succeeded
            captures["retained_dataset"] = item.id

    spec = json.loads(LIST.read_text())
    steps = [step for step in spec["direct_requests"] if step["argv"] == ["dataset", "read"]]
    assert len(steps) == 1, "the command list owns exactly one retained dataset read case"
    handles: list[ServerHandle] = []
    evidence: dict[str, Any] = {"list": LIST.name, "comparisons": {}}
    try:
        reference = _start_server(
            tmp_path / "python",
            fake_now="2026-10-27T12:00:00",
            server_module="tests.compare.host",
            seed_db=seed,
        )
        handles.append(reference)
        step = substitute(steps[0], captures)
        # SQLite backup is read-only on the running source and restores only into
        # the candidate's not-yet-running database, as the existing runner does.
        snapshot = tmp_path / "seed.db"
        backup_database(reference.db_path, snapshot)
        candidate_host = _start_server(
            tmp_path / "typescript",
            fake_now="2026-10-27T12:00:00",
            server_module="tests.compare.ts_launcher",
            seed_db=lambda target: backup_database(snapshot, target),
        )
        handles.append(candidate_host)
        # Python's action delegates to the dataset reader. TS uses database.read.
        # The POST read changes no data, so both clients reuse each existing host.
        for mode in ("json", "text"):
            python_cli = run_cli(
                CliHost(reference, tmp_path), step, False, tmp_path / "python-input", mode
            )
            ts_cli_reference = run_cli(
                CliHost(reference, tmp_path), step, True, tmp_path / "typescript-input", mode
            )
            ts_cli_candidate = run_cli(
                CliHost(candidate_host, tmp_path), step, True, tmp_path / "candidate-input", mode
            )
            evidence["comparisons"][mode] = {
                "python_cli_python_host": python_cli,
                "typescript_cli_python_host": ts_cli_reference,
                "typescript_cli_typescript_host": ts_cli_candidate,
            }
            assert comparable(python_cli) == comparable(ts_cli_reference), evidence
            assert comparable(python_cli) == comparable(ts_cli_candidate), evidence
        evidence["runtime_guidance"] = _runtime_proof(reference, candidate_host, tmp_path)
    finally:
        for handle in reversed(handles):
            _stop_server(handle)
        evidence["seconds"] = time.perf_counter() - started
        destination = request.config.getoption("--compare-report-dir")
        if destination:
            root = Path(destination)
            root.mkdir(parents=True, exist_ok=True)
            (root / "unit20-retained-dataset-cli.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )


def _runtime_proof(reference: ServerHandle, candidate: ServerHandle, root: Path) -> dict[str, Any]:
    """Fresh shared exports pin every byte and each accepted schema language path."""
    from tests.compare.direct import unit20_domain_fixtures as fixtures

    from planner.measure_sources.guidance import RUN, SCRIPT, runtime_guide

    started = time.perf_counter()
    expected_python = {
        "runtime": runtime_guide(),
        "schema-script": SCRIPT.reference(),
        "schema-run": RUN.reference(),
    }
    exported = fixtures.node_export(
        CliHost(reference, root),
        root / "runtime-export",
        """
import {runtime_guide,SCRIPT,RUN} from './server/measure_sources/guidance.ts';
import {stringify_python_json as json} from './server/core/http/numeric_json.ts';
const declarations={runtime:runtime_guide(),'schema-script':SCRIPT,'schema-run':RUN};
process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(declarations).map(
  ([name,data])=>[name,{data,json:json(data)+'\\n',
    text:json(data,{ensure_ascii:false,indent:2})+'\\n'}]))));
""",
    )
    steps = fixtures.direct_steps("measure-source")
    proof: dict[str, Any] = {"changed_paths": {}, "outputs": {}}
    for name, expected_data in expected_python.items():
        changed = fixtures.differences(
            expected_data, exported[name]["data"], indexed_arrays=name != "runtime"
        )
        if name == "runtime":
            allowed = set(fixtures.rule("runtime_guidance")["paths"])
            step = next(item for item in steps if item["argv"] == ["measure-source", "runtime"])
        else:
            changed = {name + "." + path for path in changed}
            allowed = set(fixtures.rule("source_schemas")["paths"])
            step = next(
                item
                for item in steps
                if item["argv"] == ["measure-source", "schema", name.removeprefix("schema-")]
            )
        assert changed <= allowed, (name, changed - allowed)
        proof["changed_paths"][name] = sorted(changed)
        proof["outputs"][name] = {}
        for mode in ("json", "text"):
            expected = (
                json.dumps(
                    expected_data,
                    **({} if mode == "json" else {"ensure_ascii": False, "indent": 2}),
                )
                + "\n"
            )
            left = run_cli(CliHost(reference, root), step, False, root / "runtime-py", mode)
            right = run_cli(CliHost(reference, root), step, True, root / "runtime-ts", mode)
            hosted = run_cli(CliHost(candidate, root), step, True, root / "runtime-hosted", mode)
            assert left["stdout"] == expected and left["stderr"] == ""
            assert right["stdout"] == exported[name][mode] and right["stderr"] == ""
            assert comparable(right) == comparable(hosted)
            proof["outputs"][name][mode] = [left, right, hosted]
    proof["seconds"] = time.perf_counter() - started
    return proof


def test_unit20_attention_setup_cli(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """One isolated setup per language pins exact factory text and all other source rows."""
    import shutil

    from tests.compare.direct import unit20_domain_fixtures as fixtures

    from planner.attention.collection import _script

    started = time.perf_counter()
    step = fixtures.direct_steps("attention")[0]
    handles: list[ServerHandle] = []
    evidence: dict[str, Any] = {"list": LIST.name, "implementations": []}
    baseline = tmp_path / "baseline.db"
    try:
        reference = _start_server(tmp_path / "python", server_module="tests.compare.host")
        handles.append(reference)
        backup_database(reference.db_path, baseline)
        states = []
        outputs = []
        for implementation, module, candidate_cli in (
            ("python_cli_python_host", "tests.compare.host", False),
            ("typescript_cli_python_host", "tests.compare.host", True),
            ("typescript_cli_typescript_host", "tests.compare.ts_launcher", True),
        ):
            if implementation != "python_cli_python_host":
                _stop_server(handles.pop())
                shutil.rmtree(reference.db_path.parent)
                reference = _start_server(
                    tmp_path / "python",
                    server_module=module,
                    seed_db=lambda target: backup_database(baseline, target),
                )
                handles.append(reference)
            resolved = substitute(step, {"server_database": str(reference.db_path)})
            answer = run_cli(
                CliHost(reference, tmp_path),
                resolved,
                candidate_cli,
                tmp_path / "attention-input",
                "json",
            )
            returned = json.loads(answer["stdout"])
            human = run_cli(
                CliHost(reference, tmp_path),
                resolved,
                candidate_cli,
                tmp_path / "attention-input",
                "text",
            )
            assert json.loads(human["stdout"]) == returned
            if candidate_cli:
                expected = fixtures.node_export(
                    CliHost(reference, tmp_path),
                    tmp_path / "attention-export",
                    """
import {readFileSync} from 'node:fs';
import {collection_script} from './server/attention/setup.ts';
const value=JSON.parse(readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(collection_script(value.path,value.datasets,value.measures)));
""",
                    {
                        "path": str(reference.db_path),
                        "datasets": returned["dataset_ids"],
                        "measures": returned["measure_ids"],
                    },
                )
            else:
                expected = _script(
                    reference.db_path, returned["dataset_ids"], returned["measure_ids"]
                )
            state = fixtures.attention_state(reference, returned, expected, root=tmp_path)
            evidence["implementations"].append(
                {"implementation": implementation, "json": answer, "text": human, "state": state}
            )
            states.append({"returned": state["returned"], "rows": state["rows"]})
            normalizer = Normalizer(
                json.loads(fixtures.RULES.read_text()), reference.db_path.parent
            )
            normalizer.ids[returned["source_id"]] = "<attention-source>"
            for kind in ("dataset_ids", "measure_ids"):
                for metric, identifier in returned[kind].items():
                    normalizer.ids[identifier] = f"<attention-{kind}-{metric}>"
            outputs.append(
                [normalizer.normalize(comparable(answer)), normalizer.normalize(comparable(human))]
            )
        assert states[0] == states[1] == states[2], evidence
        assert outputs[0] == outputs[1] == outputs[2], evidence
    finally:
        for handle in reversed(handles):
            _stop_server(handle)
        evidence["seconds"] = time.perf_counter() - started
        _domain_report(request, "unit20-attention-setup-cli", evidence)


def _domain_report(request: pytest.FixtureRequest, name: str, evidence: Any) -> None:
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        (root / (name + ".json")).write_text(json.dumps(evidence, indent=2) + "\n")


def test_unit20_real_preview_cli(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """One owned socket and real Git worktree prove registration without a browser."""
    import subprocess
    import sys
    from dataclasses import asdict
    from unittest.mock import patch

    from tests.compare.direct import unit20_cli as driver
    from tests.compare.direct import unit20_domain_fixtures as fixtures
    from tests.integration.test_preview_registry import application

    from planner.previews.process import LinuxProcessInspector

    if sys.platform != "linux":
        pytest.skip("real preview registration requires Linux /proc")
    started = time.perf_counter()
    handles: list[ServerHandle] = []
    evidence: dict[str, Any] = {"list": LIST.name, "implementations": []}
    ingress = {
        "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
        "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@comparison.invalid",
        "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "https://panels.comparison.invalid",
    }
    try:
        reference = _start_server(
            tmp_path / "python", server_module="tests.compare.host", trusted_ingress_env=ingress
        )
        handles.append(reference)
        git_root = tmp_path / "project"
        git_root.mkdir()
        input_root = tmp_path / "native-input"
        input_root.mkdir()
        env = driver.environment(CliHost(reference, tmp_path), {"actor": "owner"}, input_root)
        for command in (
            ["git", "init", "-q", str(git_root)],
            [
                "git",
                "-C",
                str(git_root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@comparison.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
        ):
            subprocess.run(command, env=env, check=True, capture_output=True)
        project = fixtures.api(
            reference,
            "POST",
            "/api/projects",
            {"name": "CLI preview project", "folder_path": str(git_root)},
        )
        ticket = fixtures.api(
            reference,
            "POST",
            "/api/tickets",
            {
                "title": "CLI real preview",
                "worker_type": "coding",
                "project_id": project["id"],
                "manager": {"kind": "chief", "id": "chief"},
            },
        )
        tree = git_root / "worktrees" / (ticket["id"] + "-preview")
        subprocess.run(
            ["git", "-C", str(git_root), "worktree", "add", "--detach", str(tree)],
            env=env,
            check=True,
            capture_output=True,
        )
        snapshot = tmp_path / "preview-seed.db"
        backup_database(reference.db_path, snapshot)
        candidate = _start_server(
            tmp_path / "typescript",
            server_module="tests.compare.ts_launcher",
            trusted_ingress_env=ingress,
            seed_db=lambda target: backup_database(snapshot, target),
        )
        handles.append(candidate)
        results = []
        # The reused listener helper receives only synthetic HOME/provider homes.
        with patch.dict(os.environ, env, clear=True), application(tree) as (child, port):
            actual_process = asdict(LinuxProcessInspector().discover(port, tree))
            for name, handle, candidate_cli in (
                ("python_cli_python_host", reference, False),
                ("typescript_cli_python_host", reference, True),
                ("typescript_cli_typescript_host", candidate, True),
            ):
                modes: dict[str, Any] = {}
                for mode in ("json", "text"):
                    normalizer = Normalizer(
                        json.loads(fixtures.RULES.read_text()), handle.db_path.parent
                    )
                    normalizer.ids[ticket["id"]] = "<preview-ticket>"
                    answers = []
                    generation, created_at = None, None
                    for step in fixtures.direct_steps("preview"):
                        resolved = substitute(
                            step, {"preview_ticket": ticket["id"], "preview_port": str(port)}
                        )
                        interval_start = int(time.time())
                        answer = run_cli(
                            CliHost(handle, tmp_path),
                            resolved,
                            candidate_cli,
                            tmp_path / "preview-input",
                            mode,
                        )
                        interval = interval_start, int(time.time())
                        current = fixtures.api(
                            handle, "GET", "/api/previews", identity={"ticket_id": ticket["id"]}
                        )
                        first = resolved["argv"][1] == "prepare"
                        if first:
                            generation, created_at = current["generation"], current["created_at"]
                        assert (
                            current["generation"] == generation
                            and current["created_at"] == created_at
                        )
                        if not first:
                            assert current["process"] == actual_process
                        answers.append(
                            fixtures.preview_answer(
                                answer, current, handle, interval, normalizer, mode, first=first
                            )
                        )
                        evidence["implementations"].append(
                            {
                                "implementation": name,
                                "mode": mode,
                                "argv": resolved["argv"],
                                "raw": answer,
                                "api": current,
                                "interval": interval,
                            }
                        )
                    modes[mode] = answers
                results.append(modes)
                assert child.poll() is None
        assert results[0] == results[1] == results[2], evidence
    finally:
        for handle in reversed(handles):
            _stop_server(handle)
        evidence["seconds"] = time.perf_counter() - started
        _domain_report(request, "unit20-real-preview-cli", evidence)
