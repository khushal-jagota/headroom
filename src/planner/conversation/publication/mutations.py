"""Reversible plain-field changes; no provider or event-loop work crosses this seam."""

from dataclasses import dataclass, fields, is_dataclass
from types import MemberDescriptorType


@dataclass(frozen=True, slots=True)
class PlainFieldMutation:
    target: object
    attribute: str
    value: object


@dataclass(frozen=True, slots=True)
class SignalRuntimeMutation:
    conversation_id: str
    runtime_epoch: str
    expected_turn: object | None
    operations: tuple[PlainFieldMutation, ...]


def apply_fields(mutation: SignalRuntimeMutation) -> list[PlainFieldMutation]:
    undo: list[PlainFieldMutation] = []
    try:
        for operation in mutation.operations:
            # Bind plain instance fields only. Descriptors can call external code.
            descriptor = inspect_field(operation.target, operation.attribute)
            if not descriptor:
                raise TypeError("Signal mutations require an existing plain instance field")
            undo.append(
                PlainFieldMutation(
                    operation.target,
                    operation.attribute,
                    getattr(operation.target, operation.attribute),
                )
            )
            setattr(operation.target, operation.attribute, operation.value)
    except BaseException:
        undo_fields(undo)
        raise
    return undo


def inspect_field(target: object, name: str) -> bool:
    if type(target).__setattr__ is not object.__setattr__:
        return False
    descriptor = vars(type(target)).get(name)
    if descriptor is not None and hasattr(type(descriptor), "__set__"):
        return (
            is_dataclass(target)
            and isinstance(descriptor, MemberDescriptorType)
            and name in {field.name for field in fields(target)}
        )
    return name in getattr(target, "__dict__", {})


def undo_fields(undo: list[PlainFieldMutation]) -> None:
    for operation in reversed(undo):
        setattr(operation.target, operation.attribute, operation.value)
