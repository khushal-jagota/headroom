"""Standalone stdlib-only source bootstrap, mounted without the application package."""

from __future__ import annotations

import json
import resource
import sys
from typing import Any


def main() -> None:
    limits = json.loads(sys.argv[1])
    for name, value in (
        (resource.RLIMIT_AS, limits["script_memory_bytes"]),
        (resource.RLIMIT_CPU, limits["script_cpu_seconds"]),
        (resource.RLIMIT_NOFILE, limits["script_file_descriptors"]),
        (resource.RLIMIT_FSIZE, limits["script_file_bytes"]),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(name, (value, value))
    context = json.loads(sys.stdin.buffer.readline())
    sequence = 0

    def call(op: str, **values: Any) -> Any:
        nonlocal sequence
        sequence += 1
        frame = {"op": op, "id": sequence, **values}
        sys.stdout.write(json.dumps(frame, allow_nan=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        reply = json.loads(sys.stdin.buffer.readline())
        if reply.get("id") != sequence or reply.get("op") != op:
            raise RuntimeError("host protocol refusal")
        return reply

    def request(
        grant_id: str,
        url: str,
        method: str = "GET",
        json_body: Any = None,
    ) -> Any:
        return call("request", grant_id=grant_id, url=url, method=method, json_body=json_body)[
            "response"
        ]

    def latest(measure_id: str) -> Any:
        return call("latest", measure_id=measure_id)["observation"]

    def record(
        measure_id: str,
        date: str,
        value: float | None,
        unavailable_reason: str | None = None,
    ) -> None:
        call(
            "record",
            measure_id=measure_id,
            date=date,
            value=value,
            unavailable_reason=unavailable_reason,
        )

    sys.stdout.write('{"op":"ready"}\n')
    sys.stdout.flush()
    with open("/run/source.py", encoding="utf-8") as handle:
        source = compile(handle.read(), "/run/source.py", "exec")
    namespace = {
        "__name__": "__main__",
        "context": context,
        "request": request,
        "latest": latest,
        "record": record,
    }
    exec(source, namespace)


if __name__ == "__main__":
    # Never send a Python exception, its message, or its traceback to the host.
    try:
        main()
    except MemoryError:
        sys.stdout.write('{"op":"memory_exhausted"}\n')
        sys.stdout.flush()
        sys.exit(1)
    except BaseException:
        sys.exit(1)
