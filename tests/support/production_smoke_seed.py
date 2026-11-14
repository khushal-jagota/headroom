"""Prepare the existing populated comparison fixture for a private production boot."""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
from contextlib import closing
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from tests.compare.runner import (
    backup_database,
    capture_values,
    request_body,
    substitute,
)
from tests.support.server_fixtures import _start_server, _stop_server
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)

from planner.core.db import connect, create_schema


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    data = root / "data"
    data.mkdir()
    seed = root / "seed.db"
    if seed.exists():
        raise FileExistsError(seed)
    homes = create_synthetic_homes(root)
    with synthetic_home_environment(homes), closing(connect(str(seed))) as connection:
        create_schema(connection)

    source = Path(__file__).resolve().parents[2]
    specification = source / "tests/compare/lists/unit-04.populated.json"
    steps = json.loads(specification.read_text())["seed_requests"]
    captures: dict[str, object] = {}
    host = _start_server(
        root / "seed-source",
        seed_db=lambda target: backup_database(seed, target),
        server_module="tests.compare.host",
    )
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=30
        ) as client:
            for step in steps:
                call = substitute(step, captures)
                response = client.request(
                    call["method"],
                    host.base + call["path"],
                    **request_body(call),
                    headers=call.get("headers"),
                )
                if response.status_code != call["status"]:
                    raise RuntimeError(
                        f"{call['method']} {call['path']}: "
                        f"{response.status_code}: {response.text[:1000]}"
                    )
                if step.get("capture"):
                    capture_values(response.json(), step, captures)
    finally:
        _stop_server(host)

    backup_database(host.db_path, seed)
    database = data / "planning.db"
    backup_database(seed, database)
    for name in ("files", "skills", "credential-key"):
        fixture = host.db_path.parent / name
        if fixture.is_dir():
            shutil.copytree(fixture, data / name)
        elif fixture.is_file():
            shutil.copy2(fixture, data / name)
    (root / "captures.json").write_text(json.dumps(captures, indent=2) + "\n")
    (root / "homes.json").write_text(json.dumps(homes, indent=2) + "\n")
    print(
        json.dumps(
            {"seed": str(seed), "database": str(database), "requests": len(steps)}
        )
    )


if __name__ == "__main__":
    main()
