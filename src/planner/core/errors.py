"""Structured errors. Every domain rejection raises PlannerError with a stable
code; the API renders it as {"error": {code, message, detail}} and the CLI keys
its exit code off the presence of that envelope.

The definitions live in planner.core.contracts so pure logic can import them
without leaving the contracts layer (SPEC §14); this module re-exports them and
every existing import keeps working."""

from __future__ import annotations

from planner.core.contracts import ErrorCode, PlannerError

__all__ = ["ErrorCode", "PlannerError"]
