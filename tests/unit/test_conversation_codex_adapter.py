"""What the codex adapter has to be true about, against a codex that is scripted.

One obligation is left here, and it is the one no outside observer can see: a resume that
came back with somebody else's thread is refused rather than accepted in silence.
Downstream, a fresh thread and a restored one are indistinguishable — same shape, same
wire, an agent that answers — so a substitute taken quietly is the whole conversation's
memory gone with nothing to show for it. It cannot be provoked against a real codex, which
is why it is scripted here.

The bench it runs on — the scripted app-server, the recording sink, and the child's own
transcript of what it was sent — lives in
``tests/support/conversation_codex_app_server_bench.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support.conversation_codex_app_server_bench import _run, _scripted_child

from planner.conversation.backends.contracts import SessionLoadFailed


def test_a_resume_that_came_back_with_another_thread_is_refused(tmp_path: Path) -> None:
    """The documented codex trap: a resume that quietly hands back a different thread.

    Downstream, a fresh thread and a restored one look identical — same shape, same wire,
    an agent that answers. The only place the difference is visible is here, so this is
    where it has to be caught.
    """

    async def exercise() -> None:
        script = {"resume": {"outcome": "other_thread", "thread_id": "thread-somebody-else"}}
        async with _scripted_child(tmp_path, script=script) as scripted:
            with pytest.raises(SessionLoadFailed) as refused:
                await scripted.start(cursor="thread-earlier")
            assert "thread-somebody-else" in str(refused.value)

    _run(exercise)
