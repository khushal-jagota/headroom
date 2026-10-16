"""A codex app-server that answers sign-in messages to order, on the real wire.

The sign-in service's whole job is ordering: a completion that arrives before the start
response, a completion racing a cancel, a child that dies mid-session. None of those can be
provoked against the real codex, and a mock of the client would be testing the mock. So
this is a real child process speaking real newline-delimited JSON with no envelope, doing
exactly what the script in its environment tells it to.

It holds no test of its own. It is spawned as a script — ``python <this file>`` with a
script path in the environment — rather than imported by the code under test.

Nothing here is a credential. The device code and the verification URL are whatever the
script says, and no network call is made by anything in this file.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH_ENVIRONMENT_NAME = "PANELS_CODEX_SIGN_IN_DOUBLE_SCRIPT"


def sign_in_double_launch(script_path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """The argv and the one environment name that run this file as a sign-in app-server."""
    return (
        (sys.executable, str(Path(__file__).resolve())),
        {SCRIPT_PATH_ENVIRONMENT_NAME: str(script_path)},
    )


class SignInDouble:
    """Answers ``initialize``, ``account/login/start`` and ``account/login/cancel``.

    Every behaviour a test needs is a key in the script rather than a flag in here, so a
    reader of the test can see the ordering being provoked without coming to this file.
    """

    def __init__(self, script: dict[str, Any]) -> None:
        self._script = script
        self._writing = asyncio.Lock()
        self._login_id: str = script.get("login_id", "login-1")
        self._cancel_status: str = script.get("cancel_status", "canceled")
        self._completions_sent = 0

    async def run(self) -> None:
        reader = await self._stdin_reader()
        while True:
            line = await reader.readline()
            if not line:
                return
            await self._handle(json.loads(line))

    async def _stdin_reader(self) -> asyncio.StreamReader:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        return reader

    async def _handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            await self._answer(request_id, {"userAgent": "sign-in-double/0"})
            return
        if method == "initialized":
            return
        if method == "account/login/start":
            await self._start_a_login(request_id)
            return
        if method == "account/login/cancel":
            await self._answer(request_id, {"status": self._cancel_status})
            if self._script.get("complete_after_cancel"):
                await self._send_completion(self._script["complete_after_cancel"])
            if self._script.get("exit_after_cancel"):
                sys.exit(0)
            return
        if request_id is not None:
            await self._answer(request_id, {}, error="this double answers only login calls")

    async def _start_a_login(self, request_id: Any) -> None:
        # A completion before the start response is the ordering the service parks for, so
        # the script can ask for it to be sent first and in that exact order.
        before = self._script.get("complete_before_start_response")
        if before:
            await self._send_completion(before)
        if self._script.get("exit_before_start_response"):
            sys.exit(0)
        if self._script.get("never_answer_start"):
            return
        await self._answer(
            request_id,
            {
                "type": "chatgptDeviceCode",
                "loginId": self._login_id,
                "verificationUrl": self._script.get(
                    "verification_url", "https://auth.openai.com/codex/device"
                ),
                "userCode": self._script.get("user_code", "TEST-CODE"),
            },
        )
        after = self._script.get("complete_after_start_response")
        if after:
            await self._send_completion(after)
        if self._script.get("exit_after_start_response"):
            sys.exit(0)

    async def _send_completion(self, completion: dict[str, Any]) -> None:
        payload = {
            "loginId": completion.get("login_id", self._login_id),
            "success": bool(completion.get("success")),
            "error": completion.get("error"),
            "onboardingEntrypoint": None,
        }
        if completion.get("login_id_absent"):
            payload["loginId"] = None
        await self._write({"method": "account/login/completed", "params": payload})
        self._completions_sent += 1

    async def _answer(self, request_id: Any, result: Any, *, error: str | None = None) -> None:
        if request_id is None:
            return
        if error is not None:
            await self._write({"id": request_id, "error": {"code": -1, "message": error}})
            return
        await self._write({"id": request_id, "result": result})

    async def _write(self, message: dict[str, Any]) -> None:
        async with self._writing:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()


def main() -> None:
    script_path = os.environ.get(SCRIPT_PATH_ENVIRONMENT_NAME)
    if script_path is None:  # pragma: no cover - a misconfigured launch, not a behaviour
        raise SystemExit(f"{SCRIPT_PATH_ENVIRONMENT_NAME} is not set")
    script = json.loads(Path(script_path).read_text())
    asyncio.run(SignInDouble(script).run())


if __name__ == "__main__":
    main()
