"""Checkpointable, selected-field JSONL reader, following T3's streaming projection.

Ignored strings never accumulate. A checkpoint contains syntax and whitelisted values,
not a fragment of a transcript body. Newlines delimit records even after malformed JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..contracts import UsageProvider
from .shared import COUNTER_NAMES, project_usage_line

_COUNTERS: dict[str, Any] = dict.fromkeys(COUNTER_NAMES, True)
_COUNTERS.update({"speed": True, "output_tokens_details": {"thinking_tokens": True}})
_FIELDS: dict[str, Any] = {
    "type": True,
    "timestamp": True,
    "requestId": True,
    "sessionId": True,
    "costUSD": True,
    "message": {"id": True, "model": True, "usage": _COUNTERS},
    "payload": {
        **dict.fromkeys(
            (
                "type",
                "id",
                "session_id",
                "forked_from_id",
                "parent_thread_id",
                "cli_version",
                "model",
                "turn_id",
                "thread_id",
                "root_turn_id",
                "response_id",
            ),
            True,
        ),
        "source": {"subagent": {"thread_spawn": {"parent_thread_id": True}}},
        "usage": _COUNTERS,
        "turn_token_usage": _COUNTERS,
        "thread_token_usage": _COUNTERS,
        "thread_settings": {"service_tier": True},
        "info": {"last_token_usage": _COUNTERS, "total_token_usage": _COUNTERS},
    },
}
_SPECIAL = re.compile(r'["\\\x00-\x1f]')
_SPACE = re.compile(r"[ \r\t]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_MAX_SELECTED_STRING = 128 * 1024
_MAX_DEPTH = 128


class UsageProjectionStream:
    """The only resumable text consists of approved usage/provenance fields."""

    def __init__(self, provider: UsageProvider, state: dict[str, Any] | None = None) -> None:
        self.provider = provider
        self.state = state or self._empty()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "stack": [],
            "root": None,
            "done": False,
            "token": None,
            "malformed": False,
            "started": False,
        }

    def _selection(self) -> Any:
        stack = self.state["stack"]
        if not stack:
            return _FIELDS
        frame = stack[-1]
        if not frame["selected"] or frame["kind"] != "object":
            return None
        tree: Any = _FIELDS
        for part in frame["path"]:
            tree = tree.get(part) if isinstance(tree, dict) else None
        return tree.get(frame["key"]) if isinstance(tree, dict) else None

    def _value(self, value: Any) -> None:
        stack = self.state["stack"]
        if not stack:
            self.state.update(root=value, done=True)
            return
        frame = stack[-1]
        if frame["selected"] and frame["kind"] == "object" and self._selection() is not None:
            frame["value"][frame["key"]] = value
        frame["expect"] = "comma"

    def _start_string(self, *, key: bool = False) -> None:
        selection = self._selection()
        self.state["token"] = {
            "kind": "string",
            "key": key,
            "selected": selection is not None and not key,
            "value": "",
            "escape": False,
            "unicode": None,
            "possible": True,
        }

    def _append_string(self, value: str) -> None:
        token = self.state["token"]
        if token["key"]:
            if not token["possible"]:
                return
            candidate = token["value"] + value
            # Unknown keys, including long tool-output keys, never enter a checkpoint.
            tree: Any = _FIELDS
            for part in self.state["stack"][-1]["path"]:
                tree = tree.get(part) if isinstance(tree, dict) else None
            if not isinstance(tree, dict) or not any(key.startswith(candidate) for key in tree):
                token.update(value="", possible=False)
            else:
                token["value"] = candidate
        elif token["selected"]:
            if len(token["value"]) + len(value) > _MAX_SELECTED_STRING:
                self.state["malformed"] = True
            else:
                token["value"] += value

    def _end_string(self) -> None:
        token = self.state["token"]
        if token["key"]:
            frame = self.state["stack"][-1]
            frame.update(key=token["value"] if token["possible"] else None, expect="colon")
        else:
            self._value(token["value"] if token["selected"] else None)
        self.state["token"] = None

    def _end_scalar(self) -> None:
        token = self.state["token"]
        try:
            literal = token.get("literal")
            if literal is not None:
                valid = token["position"] == len(literal)
            else:
                valid = token["number"] in ("zero", "integer", "fraction", "exponent_digits")
            if not valid:
                raise ValueError("Invalid scalar")
            value = json.loads(token["value"]) if token["selected"] else None
        except (ValueError, OverflowError):
            self.state["malformed"] = True
        else:
            self._value(value if token["selected"] else None)
        self.state["token"] = None

    def _scalar_character(self, char: str) -> None:
        token = self.state["token"]
        literal = token.get("literal")
        if literal is not None:
            position = token["position"]
            if position >= len(literal) or literal[position] != char:
                self.state["malformed"] = True
            token["position"] += 1
        else:
            previous = token["number"]
            if char.isascii() and char.isdigit():
                state = {
                    "sign": "zero" if char == "0" else "integer",
                    "integer": "integer",
                    "dot": "fraction",
                    "fraction": "fraction",
                    "exponent": "exponent_digits",
                    "exponent_sign": "exponent_digits",
                    "exponent_digits": "exponent_digits",
                }.get(previous)
            elif char == "." and previous in ("zero", "integer"):
                state = "dot"
            elif char in "eE" and previous in ("zero", "integer", "fraction"):
                state = "exponent"
            elif char in "+-" and previous == "exponent":
                state = "exponent_sign"
            else:
                state = None
            if state is None:
                self.state["malformed"] = True
            else:
                token["number"] = state
        if token["selected"]:
            token["value"] += char
            if len(token["value"]) > 128:
                self.state["malformed"] = True

    def feed(self, value: str) -> list[str]:
        out: list[str] = []
        index = 0
        while index < len(value):
            char = value[index]
            if char == "\n":
                if self.state["token"] is not None and self.state["token"]["kind"] == "scalar":
                    self._end_scalar()
                if self.state["started"]:
                    if self.state["malformed"] or not self.state["done"]:
                        out.append('{"type":"usage_parse_error"}')
                    else:
                        projection = project_usage_line(
                            self.provider,
                            json.dumps(self.state["root"]),
                        )
                        if projection is not None:
                            out.append(projection)
                self.state = self._empty()
                index += 1
                continue
            if self.state["malformed"]:
                end = value.find("\n", index)
                index = len(value) if end < 0 else end
                continue
            token = self.state["token"]
            if token is not None and token["kind"] == "string":
                if token["unicode"] is not None:
                    if char not in "0123456789abcdefABCDEF":
                        self.state["malformed"] = True
                    else:
                        token["unicode"] += (
                            char
                            if token["selected"] or (token["key"] and token["possible"])
                            else "0"
                        )
                        if len(token["unicode"]) == 4:
                            self._append_string(chr(int(token["unicode"], 16)))
                            token["unicode"] = None
                    index += 1
                    continue
                if token["escape"]:
                    token["escape"] = False
                    if char == "u":
                        token["unicode"] = ""
                    elif char in '\\"/bfnrt':
                        self._append_string(
                            {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}.get(char, char)
                        )
                    else:
                        self.state["malformed"] = True
                    index += 1
                    continue
                match = _SPECIAL.search(value, index)
                end = match.start() if match else len(value)
                if end > index:
                    self._append_string(value[index:end])
                    index = end
                    continue
                if char == '"':
                    self._end_string()
                elif char == "\\":
                    token["escape"] = True
                else:
                    self.state["malformed"] = True
                index += 1
                continue
            if token is not None:
                if char in " \r\t,}]":
                    self._end_scalar()
                    continue
                self._scalar_character(char)
                index += 1
                continue
            if char in " \r\t":
                index = _SPACE.match(value, index).end()  # type: ignore[union-attr]
                continue
            self.state["started"] = True
            stack = self.state["stack"]
            frame: Any = stack[-1] if stack else None
            expect = frame["expect"] if frame else "value"
            if self.state["done"]:
                self.state["malformed"] = True
            elif expect in ("key", "key_or_end"):
                if char == "}" and expect == "key_or_end":
                    closed = stack.pop()
                    self._value(closed["value"] if closed["selected"] else None)
                elif char == '"':
                    self._start_string(key=True)
                else:
                    self.state["malformed"] = True
            elif expect == "colon":
                if char == ":":
                    frame["expect"] = "value"
                else:
                    self.state["malformed"] = True
            elif expect == "comma":
                if char == ",":
                    frame["expect"] = "key" if frame["kind"] == "object" else "value"
                elif char == ("}" if frame["kind"] == "object" else "]"):
                    closed = stack.pop()
                    self._value(closed["value"] if closed["selected"] else None)
                else:
                    self.state["malformed"] = True
            elif expect == "array_or_end" and char == "]":
                stack.pop()
                self._value([])
            elif char in "{[":
                selected = self._selection() is not None
                path = [] if frame is None else [*frame["path"], frame["key"]]
                stack.append(
                    {
                        "kind": "object" if char == "{" else "array",
                        "path": path,
                        "selected": selected,
                        "key": None,
                        "expect": "key_or_end" if char == "{" else "array_or_end",
                        "value": {},
                    }
                )
                if len(stack) > _MAX_DEPTH:
                    self.state["malformed"] = True
            elif char == '"':
                self._start_string()
            elif char in "-0123456789tfn":
                selected = self._selection() is not None
                literal = {"t": "true", "f": "false", "n": "null"}.get(char)
                self.state["token"] = {
                    "kind": "scalar",
                    "value": char if selected else "",
                    "selected": selected,
                    "literal": literal,
                    "position": 1,
                    "number": "sign" if char == "-" else "zero" if char == "0" else "integer",
                }
            else:
                self.state["malformed"] = True
            index += 1
        return out
