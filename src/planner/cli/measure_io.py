"""Small input/output helpers for measure commands; domain rules remain on the server."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, NoReturn

import click

from planner.cli import http
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT

# Bound transport input independently of the tighter domain limits.
MAX_INPUT_BYTES = 2 * 1024 * 1024


def json_option(function: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--json", "as_json", is_flag=True, help="Print the canonical JSON response."
    )(function)


def page_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option("--offset", type=int, default=0, show_default=True)(function)
    return click.option("--limit", type=int, default=DEFAULT_LIST_LIMIT, show_default=True)(
        function
    )


def stdin_object(as_json: bool) -> dict[str, Any]:
    raw = click.get_binary_stream("stdin").read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        http.fail_validation("JSON stdin exceeds two MiB.", as_json)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        http.fail_validation("Provide a JSON object on stdin.", as_json)
    if not isinstance(body, dict):
        http.fail_validation("Provide a JSON object on stdin.", as_json)
    return body


def emit(data: Any, as_json: bool) -> NoReturn:
    """Keep records inspectable and preserve explicit pagination facts in text output."""
    human = json.dumps(data, ensure_ascii=False, indent=2)
    if (
        isinstance(data, dict)
        and isinstance(data.get("page"), dict)
        and {"return_count", "match_count", "omitted_before", "omitted_after"}
        <= data["page"].keys()
    ):
        page = data["page"]
        human += (
            f"\nReturned {page['return_count']} of {page['match_count']} matches."
            f"\nOmitted {page['omitted_before']} before and {page['omitted_after']} after."
        )
        if page.get("next_offset") is not None:
            human += (
                f"\nUse --limit {page['limit']} --offset {page['next_offset']} for the next page."
            )
    http.emit(data, as_json, human)
