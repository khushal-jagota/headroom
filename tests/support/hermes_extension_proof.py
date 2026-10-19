"""Reusable offline scripts for the real Hermes interpreter compatibility proof."""

import textwrap
from pathlib import Path


def lifecycle_script(extension_directory: Path) -> str:
    script = textwrap.dedent(
        f"""
        import asyncio
        import json
        import sys
        import threading
        from types import SimpleNamespace

        sys.path.insert(0, {str(extension_directory)!r})
        import hermes_acp_extension as extension

        class Agent:
            def __init__(self):
                self.api_mode = "codex_responses"
                self.redirects = []
                self.accept = True

            def redirect(self, text):
                self.redirects.append(text)
                return self.accept

        state = SimpleNamespace(
            runtime_lock=threading.Lock(),
            is_running=False,
            cancel_event=threading.Event(),
            queued_prompts=["existing"],
            agent=Agent(),
        )
        manager = SimpleNamespace(
            get_session=lambda session_id: state if session_id == "s" else None
        )
        subject = object.__new__(extension.PanelsHermesACPAgent)
        subject.session_manager = manager
        subject._panels_prompt_generations = {{}}
        release = asyncio.Event()

        async def base_prompt(self, prompt, session_id, **kwargs):
            state.is_running = True
            await release.wait()
            state.is_running = False
            return "done"

        async def base_cancel(self, session_id, **kwargs):
            state.cancel_event.set()

        extension.hermes_server.HermesACPAgent.prompt = base_prompt
        extension.hermes_server.HermesACPAgent.cancel = base_cancel

        async def exercise():
            token = {{"conversationId": "c", "turnNumber": 7}}
            prompt = asyncio.create_task(subject.prompt([], "s", panelsTurnToken=token))
            await asyncio.sleep(0)
            accepted = await subject.ext_method(
                "panels/steer",
                {{"sessionId": "s", "turnToken": token, "text": "new direction"}},
            )
            stale = await subject.ext_method(
                "panels/steer",
                {{
                    "sessionId": "s",
                    "turnToken": {{"conversationId": "c", "turnNumber": 6}},
                    "text": "old",
                }},
            )
            state.agent.accept = False
            redirect_false = await subject.ext_method(
                "panels/steer",
                {{"sessionId": "s", "turnToken": token, "text": "not accepted"}},
            )
            state.agent.accept = True
            second_token = {{"conversationId": "c", "turnNumber": 8}}
            concurrent = asyncio.create_task(
                subject.prompt([], "s", panelsTurnToken=second_token)
            )
            await asyncio.sleep(0)
            concurrent_steer = await subject.ext_method(
                "panels/steer",
                {{"sessionId": "s", "turnToken": second_token, "text": "wrong prompt"}},
            )
            state.agent.api_mode = "codex_app_server"
            native = await subject.ext_method(
                "panels/steer",
                {{"sessionId": "s", "turnToken": token, "text": "native"}},
            )
            state.agent.api_mode = "codex_responses"
            await subject.cancel("s")
            after_cancel = await subject.ext_method(
                "panels/steer",
                {{"sessionId": "s", "turnToken": token, "text": "late"}},
            )
            release.set()
            await asyncio.gather(prompt, concurrent)
            after_completion = await subject.ext_method(
                "panels/steer",
                {{"sessionId": "s", "turnToken": token, "text": "finished"}},
            )
            return {{
                "accepted": accepted,
                "stale": stale,
                "redirect_false": redirect_false,
                "concurrent_steer": concurrent_steer,
                "native": native,
                "after_cancel": after_cancel,
                "after_completion": after_completion,
                "redirects": state.agent.redirects,
                "queued": state.queued_prompts,
            }}

        print(json.dumps(asyncio.run(exercise())))
        """
    )
    return script


def recovery_script(extension_directory: Path) -> str:
    script = textwrap.dedent(
        f"""
        import asyncio
        import json
        import sys
        import threading
        from types import SimpleNamespace

        sys.path.insert(0, {str(extension_directory)!r})
        import hermes_acp_extension as extension

        def make_state(*, running=False, cancelled=False):
            cancel_event = threading.Event()
            if cancelled:
                cancel_event.set()
            return SimpleNamespace(
                runtime_lock=threading.Lock(),
                is_running=running,
                cancel_event=cancel_event,
                current_prompt_text="incumbent" if running else "",
                agent=SimpleNamespace(api_mode="codex_responses", redirect=lambda text: True),
                queued_prompts=[],
            )

        def make_subject(state):
            subject = object.__new__(extension.PanelsHermesACPAgent)
            subject.session_manager = SimpleNamespace(get_session=lambda session_id: state)
            subject._panels_prompt_generations = {{}}
            return subject

        async def owned_fault():
            state = make_state()
            subject = make_subject(state)
            release = asyncio.Event()

            async def prompt(self, prompt, session_id, **kwargs):
                state.is_running = True
                state.current_prompt_text = "owned"
                await release.wait()
                result = {{"final_response": None}}
                final_response = result.get("final_response", "")
                final_response.startswith("waiting")

            extension.hermes_server.HermesACPAgent.prompt = prompt
            token = {{"conversationId": "c", "turnNumber": 1}}
            task = asyncio.create_task(subject.prompt([], "s", panelsTurnToken=token))
            await asyncio.sleep(0)
            state.cancel_event.set()
            release.set()
            response = await task
            return str(response.stop_reason), state.is_running, state.current_prompt_text

        async def unrelated_fault():
            state = make_state()
            subject = make_subject(state)
            release = asyncio.Event()

            async def prompt(self, prompt, session_id, **kwargs):
                state.is_running = True
                state.current_prompt_text = "owned"
                await release.wait()
                raise AttributeError("persistence failed")

            extension.hermes_server.HermesACPAgent.prompt = prompt
            token = {{"conversationId": "c", "turnNumber": 1}}
            task = asyncio.create_task(subject.prompt([], "s", panelsTurnToken=token))
            await asyncio.sleep(0)
            state.cancel_event.set()
            release.set()
            try:
                await task
            except AttributeError as failure:
                return str(failure), state.is_running, state.current_prompt_text
            raise AssertionError("unrelated failure was hidden")

        async def non_owning_fault():
            state = make_state(running=True, cancelled=True)
            subject = make_subject(state)
            subject._panels_prompt_generations["s"] = ("c", 1)

            async def prompt(self, prompt, session_id, **kwargs):
                result = {{"final_response": None}}
                final_response = result.get("final_response", "")
                final_response.startswith("waiting")

            extension.hermes_server.HermesACPAgent.prompt = prompt
            token = {{"conversationId": "c", "turnNumber": 2}}
            try:
                await subject.prompt([], "s", panelsTurnToken=token)
            except AttributeError as failure:
                state.cancel_event.clear()
                incumbent = await subject.ext_method("panels/steer", {{
                    "sessionId": "s", "turnToken": {{"conversationId": "c", "turnNumber": 1}},
                    "text": "Continue the incumbent turn",
                }})
                return str(failure), state.is_running, state.current_prompt_text, incumbent
            raise AssertionError("non-owning failure was hidden")

        async def exercise():
            return {{
                "owned": await owned_fault(),
                "unrelated": await unrelated_fault(),
                "non_owning": await non_owning_fault(),
            }}

        print(json.dumps(asyncio.run(exercise())))
        """
    )
    return script
