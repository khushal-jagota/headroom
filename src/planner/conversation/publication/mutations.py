"""Runtime field updates that accompany conversation saves."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlainFieldMutation:
    target: object
    attribute: str
    value: object


@dataclass(frozen=True, slots=True)
class SignalRuntimeMutation:
    conversation_id: str
    operations: tuple[PlainFieldMutation, ...]
