"""Panels' turn-bound steering extension for the installed Hermes ACP agent.

Hermes' stock ACP prompt route can redirect an active turn, but its response does not
identify which Panels turn accepted the text. It can also queue the text as later work.
This entry point keeps Hermes' normal startup and adds one narrow extension request whose
answer is correlated with the token on the original prompt.

This module runs with Hermes' configured Python interpreter. It deliberately imports
Hermes only here, outside the Panels server process and its dependency environment.
"""

from __future__ import annotations

from typing import Any

from acp.exceptions import RequestError
from acp_adapter import server as hermes_server
from acp_adapter.entry import main as hermes_acp_main

PANELS_STEER_EXTENSION_METHOD = "panels/steer"
PANELS_TURN_TOKEN_METADATA_KEY = "panelsTurnToken"
PANELS_CURRENT_MODEL_METADATA_KEY = "currentModelId"
PANELS_METADATA_KEY = "panels"
HERMES_UNPROVEN_NATIVE_STEER_API_MODE = "codex_app_server"


def _turn_token(value: Any) -> tuple[str, int] | None:
    """Return the exact Panels turn identity from its wire value, if it is valid."""
    if not isinstance(value, dict):
        return None
    conversation_id = value.get("conversationId")
    turn_number = value.get("turnNumber")
    if not isinstance(conversation_id, str) or not conversation_id:
        return None
    if not isinstance(turn_number, int) or isinstance(turn_number, bool) or turn_number < 1:
        return None
    return conversation_id, turn_number


class PanelsHermesACPAgent(hermes_server.HermesACPAgent):
    """Hermes ACP with correlated admission for one live Panels turn."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._panels_prompt_generations: dict[str, tuple[str, int]] = {}

    async def new_session(self, *args: Any, **kwargs: Any) -> Any:
        response = await super().new_session(*args, **kwargs)
        return self._with_current_model(response)

    async def load_session(self, *args: Any, **kwargs: Any) -> Any:
        response = await super().load_session(*args, **kwargs)
        return self._with_current_model(response)

    def _with_current_model(self, response: Any) -> Any:
        """Copy Hermes' exact model state into stable ACP metadata for Panels 0.11."""
        if response is None:
            return None
        models = getattr(response, "models", None)
        current_model_id = getattr(models, "current_model_id", None)
        if not isinstance(current_model_id, str) or not current_model_id:
            return response
        metadata = dict(getattr(response, "field_meta", None) or {})
        panels_metadata = dict(metadata.get(PANELS_METADATA_KEY) or {})
        panels_metadata[PANELS_CURRENT_MODEL_METADATA_KEY] = current_model_id
        metadata[PANELS_METADATA_KEY] = panels_metadata
        return response.model_copy(update={"field_meta": metadata})

    async def prompt(
        self, prompt: list[Any], session_id: str, **kwargs: Any
    ) -> Any:
        """Associate a supplied generation only with this original prompt invocation."""
        supplied = _turn_token(kwargs.pop(PANELS_TURN_TOKEN_METADATA_KEY, None))
        state = self.session_manager.get_session(session_id)
        owns_generation = False
        if state is not None:
            with state.runtime_lock:
                if supplied is None:
                    # Hermes recursively runs queued prompts after it marks the original
                    # turn idle. Those prompts have no Panels token and must not inherit it.
                    if not state.is_running:
                        self._panels_prompt_generations.pop(session_id, None)
                elif not state.is_running and session_id not in self._panels_prompt_generations:
                    self._panels_prompt_generations[session_id] = supplied
                    owns_generation = True
        try:
            return await super().prompt(prompt=prompt, session_id=session_id, **kwargs)
        except Exception:
            # Hermes 0.20 can dereference a missing final response after a hard
            # interrupt. Finish the cancelled lifecycle here so a later prompt does
            # not see the session as active and fall into Hermes' stock queue.
            cancelled = False
            if state is not None:
                with state.runtime_lock:
                    cancelled = bool(state.cancel_event and state.cancel_event.is_set())
                    if cancelled:
                        state.is_running = False
                        state.current_prompt_text = ""
            if not cancelled:
                raise
            return hermes_server.PromptResponse(stop_reason="cancelled")
        finally:
            if state is not None and owns_generation:
                with state.runtime_lock:
                    if self._panels_prompt_generations.get(session_id) == supplied:
                        self._panels_prompt_generations.pop(session_id, None)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Invalidate the generation before Hermes publishes the hard cancellation."""
        state = self.session_manager.get_session(session_id)
        if state is not None:
            with state.runtime_lock:
                self._panels_prompt_generations.pop(session_id, None)
        await super().cancel(session_id=session_id, **kwargs)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != PANELS_STEER_EXTENSION_METHOD:
            raise RequestError.method_not_found(f"_{method}")

        session_id = params.get("sessionId")
        supplied_value = params.get("turnToken")
        supplied = _turn_token(supplied_value)
        text = params.get("text")
        if (
            not isinstance(session_id, str)
            or supplied is None
            or not isinstance(text, str)
            or not text.strip()
        ):
            return {"accepted": False, "turnToken": supplied_value}

        state = self.session_manager.get_session(session_id)
        if state is None:
            return {"accepted": False, "turnToken": supplied_value}

        # redirect() is synchronous. The session lock makes generation validation and
        # admission one operation without awaiting under Hermes' non-reentrant lock.
        with state.runtime_lock:
            if self._panels_prompt_generations.get(session_id) != supplied:
                return {"accepted": False, "turnToken": supplied_value}
            if not state.is_running or (state.cancel_event and state.cancel_event.is_set()):
                return {"accepted": False, "turnToken": supplied_value}
            agent = state.agent
            if getattr(agent, "api_mode", None) == HERMES_UNPROVEN_NATIVE_STEER_API_MODE:
                return {"accepted": False, "turnToken": supplied_value}
            accepted = bool(agent.redirect(text))
        return {"accepted": accepted, "turnToken": supplied_value}


def main() -> None:
    """Run Hermes' normal ACP entry point with the Panels subclass installed."""
    hermes_server.HermesACPAgent = PanelsHermesACPAgent
    hermes_acp_main([])


if __name__ == "__main__":
    main()
