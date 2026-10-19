"""Decision evidence for the held ordinary-send receipt extension.

The scripted provider admits one request and answers with an unreadable result.
The existing public result says refused. This test documents that gap without
changing receipt semantics or claiming evidence from a real provider.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChild,
    CodexChildLaunch,
)
from planner.conversation.backends.codex_app_server.login import RememberedCodexLoginRejection
from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    ResolvedConversationStart,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema


def test_an_admitted_scripted_prompt_can_still_return_public_refusal(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    script = tmp_path / "admitted_bad_response.py"
    transcript = tmp_path / "provider.jsonl"
    script.write_text(
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from tests.support.conversation_codex_scripted_app_server "
        "import ScriptedAppServer\n"
        "class AdmittedBadResponse(ScriptedAppServer):\n"
        "    async def _run_turn(self, request_id, parameters, **kwargs):\n"
        "        self._write_down({'admitted': parameters['input']})\n"
        "        await self._respond(request_id, {'turn': {}})\n"
        "asyncio.run(AdmittedBadResponse("
        f"{{'transcript_path': {str(transcript)!r}}}).run())\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "planner.db")
    conn = connect(db_path)
    try:
        create_schema(conn)
    finally:
        conn.close()

    def factory(
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> CodexAppServerBackendChild:
        return CodexAppServerBackendChild(
            launch=CodexChildLaunch(argv=(sys.executable, str(script))),
            resolved_start=resolved_start,
            event_sink=event_sink,
            message_files=message_files,
            remembered_login_rejection=RememberedCodexLoginRejection(),
        )

    async def exercise() -> None:
        system = SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(db_path),
        )
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    conversation_id="receipt-evidence",
                    backend_key=ConversationBackendKey.codex,
                    model="scripted-model",
                    workspace_folder=tmp_path,
                )
            )
            result = await system.send(
                "receipt-evidence",
                text_message_content("admit exactly once"),
                sender_label="owner",
            )
            assert result == PromptDeliveryRefused(
                PromptDeliveryRefusalReason.write_to_backend_failed
            )
            rows = [json.loads(line) for line in transcript.read_text().splitlines()]
            admissions = [row["admitted"] for row in rows if "admitted" in row]
            assert len(admissions) == 1
            assert "admit exactly once" in json.dumps(admissions[0])
            assert not await system.is_running("receipt-evidence")
        finally:
            await system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 15))
