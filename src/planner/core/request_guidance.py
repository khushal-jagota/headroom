"""Generate request documentation from contracts without validating incoming bodies."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NotRequired, get_origin, get_type_hints

from pydantic import GetJsonSchemaHandler, TypeAdapter
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


def unknown_field_path(parent: str, name: object) -> str:
    """Only ordinary bounded identifiers may appear in public diagnostics."""
    if isinstance(name, str) and name.isascii() and len(name) <= 64 and name.isidentifier():
        return f"{parent}.{name}"
    return parent


@dataclass(frozen=True)
class RequestGuidance:
    """One docs-only schema provider shared by CLI and OpenAPI.

    Normalization metadata is keyed by contract types, never schema titles or
    field-name guesses. The copied core schema is used only to produce JSON Schema.
    Runtime requests remain dictionaries handled by their existing domain parsers.
    """

    contract: Any
    examples: Mapping[str, dict[str, Any]]
    notes: tuple[str, ...]
    optional_fields: Mapping[Any, frozenset[str]] = field(default_factory=dict)
    required_fields: Mapping[Any, tuple[str, ...]] = field(default_factory=dict)
    defaults: Mapping[Any, Mapping[str, Any]] = field(default_factory=dict)
    closed_objects: frozenset[Any] = field(default_factory=frozenset)

    def _schema(self) -> CoreSchema:
        schema = copy.deepcopy(TypeAdapter(self.contract).core_schema)

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("cls") in self.closed_objects:
                    metadata = node.setdefault("metadata", {})
                    metadata.setdefault("pydantic_js_updates", {})["additionalProperties"] = False
                if node.get("type") == "typed-dict":
                    contract = node.get("cls")
                    node["extra_behavior"] = "forbid"
                    hints = get_type_hints(contract, include_extras=True)
                    for name, item in node["fields"].items():
                        if get_origin(hints[name]) is NotRequired:
                            item["required"] = False
                        if name in self.optional_fields.get(contract, ()):
                            item["required"] = False
                        if name in self.required_fields.get(contract, ()):
                            item["required"] = True
                        if name in self.defaults.get(contract, {}):
                            item["schema"] = {
                                "type": "default",
                                "schema": item["schema"],
                                "default": self.defaults[contract][name],
                            }
                for value in tuple(node.values()):
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(schema)
        return schema

    def __get_pydantic_json_schema__(
        self, _schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # Let the surrounding generator register recursive component references.
        return handler(self._schema())

    def reference(self) -> dict[str, Any]:
        from pydantic.json_schema import GenerateJsonSchema

        return {
            "schema": GenerateJsonSchema().generate(self._schema()),
            "examples": copy.deepcopy(dict(self.examples)),
            "notes": list(self.notes),
        }

    def openapi_examples(self) -> dict[str, Any]:
        return {name: {"value": copy.deepcopy(value)} for name, value in self.examples.items()}

    @property
    def description(self) -> str:
        return "\n\n".join(self.notes)
