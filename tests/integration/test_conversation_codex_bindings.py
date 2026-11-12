"""The current CLI must accept Panels requests and emit decodable protocol messages."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from planner.conversation.backends.codex_app_server import generate_bindings as generator

# The server sends these requests to Panels. Other Params roots go to the server.
_SERVER_REQUESTS = {
    "CommandExecutionRequestApprovalParams", "FileChangeRequestApprovalParams",
    "ToolRequestUserInputParams",
}
_CLIENT_RESPONSES = {
    "CommandExecutionRequestApprovalResponse", "FileChangeRequestApprovalResponse",
    "ToolRequestUserInputResponse",
}


def _accepts(
    source: dict[str, Any] | bool, receiver: dict[str, Any] | bool,
    source_definitions: dict[str, Any], receiver_definitions: dict[str, Any],
    *, path: str, seen: frozenset[tuple[int, int]] = frozenset(),
) -> None:
    """Check the schema vocabulary used by the vendored protocol in wire direction."""
    if receiver is True or source is False:
        return
    assert receiver is not False and source is not True, f"{path}: incompatible open schema"
    while "$ref" in source or "allOf" in source:
        assert not {"$ref", "allOf"} <= source.keys(), f"{path}: mixed reference combiners"
        assert not source.keys() - {
            "$ref", "allOf", "description", "title", "default", "$schema",
        }, f"{path}: reference intersection needs a compatibility decision"
        source = (source_definitions[source["$ref"].removeprefix("#/definitions/")]
                  if "$ref" in source else _single_all_of(source))
    while "$ref" in receiver or "allOf" in receiver:
        assert not {"$ref", "allOf"} <= receiver.keys(), f"{path}: mixed reference combiners"
        assert not receiver.keys() - {
            "$ref", "allOf", "description", "title", "default", "$schema",
        }, f"{path}: reference intersection needs a compatibility decision"
        receiver = (receiver_definitions[receiver["$ref"].removeprefix("#/definitions/")]
                    if "$ref" in receiver else _single_all_of(receiver))
    pair = (id(source), id(receiver))
    if pair in seen:
        return
    seen = seen | {pair}
    known = {
        "type", "properties", "required", "additionalProperties", "items", "enum",
        "oneOf", "anyOf", "description", "title", "default", "$schema", "format",
        "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "pattern",
    }
    assert not (source.keys() | receiver.keys()) - known, f"{path}: unknown schema constraint"
    if not receiver.keys() - {"description", "title", "default", "$schema"}:
        return
    sources = _variants(source)
    receivers = _variants(receiver)
    if (len(sources) != 1 or len(receivers) != 1
            or sources[0] is not source or receivers[0] is not receiver):
        for variant in sources:
            failures = []
            for candidate in receivers:
                try:
                    _accepts(variant, candidate, source_definitions, receiver_definitions,
                             path=path, seen=seen)
                    break
                except AssertionError as error:
                    failures.append(str(error))
            else:
                raise AssertionError(f"{path}: incompatible union: {failures}")
        return
    assert source.get("type") == receiver.get("type"), f"{path}: changed wire type"
    if "enum" in receiver:
        assert "enum" in source and set(source["enum"]) <= set(receiver["enum"]), (
            f"{path}: receiver rejects an emitted enum value"
        )
    for key in ("format", "pattern"):
        if key in receiver:
            assert source.get(key) == receiver[key], f"{path}: changed {key} constraint"
    for key in ("minimum", "minItems", "minLength"):
        if key in receiver:
            assert source.get(key, float("-inf")) >= receiver[key], f"{path}: narrowed {key}"
    for key in ("maximum", "maxItems", "maxLength"):
        if key in receiver:
            assert source.get(key, float("inf")) <= receiver[key], f"{path}: narrowed {key}"
    if source.get("type") == "object":
        assert set(receiver.get("required", [])) <= set(source.get("required", [])), (
            f"{path}: receiver requires a field the sender does not guarantee"
        )
        source_properties = source.get("properties", {})
        receiver_properties = receiver.get("properties", {})
        for name, shape in source_properties.items():
            target = receiver_properties.get(name, receiver.get("additionalProperties", True))
            assert target is not False, f"{path}.{name}: receiver rejects this field"
            if isinstance(target, dict):
                _accepts(shape, target, source_definitions, receiver_definitions,
                         path=f"{path}.{name}", seen=seen)
        additional = source.get("additionalProperties", True)
        target_additional = receiver.get("additionalProperties", True)
        if additional is True:
            assert target_additional is True, f"{path}: receiver narrows open map entries"
        if isinstance(additional, dict):
            assert target_additional is not False, f"{path}: receiver rejects map entries"
            if isinstance(target_additional, dict):
                _accepts(additional, target_additional, source_definitions, receiver_definitions,
                         path=f"{path}.*", seen=seen)
    if source.get("type") == "array":
        _accepts(source["items"], receiver["items"], source_definitions, receiver_definitions,
                 path=f"{path}[]", seen=seen)


def _single_all_of(schema: dict[str, Any]) -> dict[str, Any]:
    assert len(schema["allOf"]) == 1, "new allOf composition needs a compatibility decision"
    return cast(dict[str, Any], schema["allOf"][0])


def _variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    if "anyOf" in schema or "oneOf" in schema:
        assert not {"anyOf", "oneOf"} <= schema.keys(), "mixed union combiners"
        branches: list[dict[str, Any]] = schema["anyOf"] if "anyOf" in schema else schema["oneOf"]
        base = {key: value for key, value in schema.items() if key not in {"anyOf", "oneOf"}}
        variants = []
        for branch in branches:
            combined = {**base, **branch}
            for key in base.keys() & branch.keys() - {
                "properties", "required", "description", "title", "default", "$schema",
            }:
                assert base[key] == branch[key], f"new intersecting {key} constraint"
            if "properties" in base or "properties" in branch:
                for name in base.get("properties", {}).keys() & branch.get("properties", {}).keys():
                    assert base["properties"][name] == branch["properties"][name], (
                        f"new intersecting property constraint: {name}"
                    )
                combined["properties"] = {**base.get("properties", {}),
                                          **branch.get("properties", {})}
            if "required" in base or "required" in branch:
                combined["required"] = list(set(base.get("required", []))
                                            | set(branch.get("required", [])))
            variants.append(combined)
        if "oneOf" in schema:
            for index, left in enumerate(variants):
                for right in variants[index + 1:]:
                    assert _disjoint(left, right), "ambiguous oneOf needs a compatibility decision"
        return variants
    if isinstance(schema.get("type"), list):
        return [{**schema, "type": kind} for kind in schema["type"]]
    return [schema]


def _disjoint(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """The protocol uses distinct primitive values or tagged object alternatives."""
    if "type" in left and "type" in right:
        a_types = {left["type"]} if isinstance(left["type"], str) else set(left["type"])
        b_types = {right["type"]} if isinstance(right["type"], str) else set(right["type"])
        # JSON Schema numbers include integers.
        a_types |= {"integer"} if "number" in a_types else set()
        b_types |= {"integer"} if "number" in b_types else set()
        if not a_types & b_types:
            return True
    if "enum" in left and "enum" in right:
        return not set(left["enum"]) & set(right["enum"])
    if left.get("type") == right.get("type") == "object":
        shared = set(left.get("required", [])) & set(right.get("required", []))
        for name in shared:
            a = left.get("properties", {}).get(name, {})
            b = right.get("properties", {}).get(name, {})
            if (isinstance(a, dict) and isinstance(b, dict)
                    and "enum" in a and "enum" in b and not set(a["enum"]) & set(b["enum"])):
                return True
        # Externally tagged Rust enums reject the other variant's required key.
        for source, receiver in ((left, right), (right, left)):
            if receiver.get("additionalProperties") is False and (
                set(source.get("required", [])) - receiver.get("properties", {}).keys()
            ):
                return True
    return False


def test_current_cli_protocol_accepts_panels_and_panels_accepts_its_messages(
    tmp_path: Path,
) -> None:
    executable = shutil.which("codex")
    assert executable is not None, "Install the current Codex CLI to qualify its protocol"
    dump = generator._dump_schema(executable, tmp_path)
    print(f"Protocol qualification: {generator._installed_codex_version(executable)}")
    for bundle in generator.BUNDLES:
        baseline = json.loads(bundle.vendored_subset_schema_path.read_text())["definitions"]
        current = generator._pruned_subset(dump, roots=bundle.roots)["definitions"]
        for _, name in bundle.roots:
            outgoing = (name.endswith("Params") and name not in _SERVER_REQUESTS
                        or name in _CLIENT_RESPONSES)
            source, receiver = (baseline, current) if outgoing else (current, baseline)
            _accepts(source[name], receiver[name], source, receiver, path=name)


def test_protocol_policy_allows_optional_additions_and_rejects_breaking_fields() -> None:
    baseline: dict[str, Any] = {
        "type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"],
    }
    added = deepcopy(baseline)
    added["properties"]["label"] = {"type": "string"}
    _accepts(baseline, added, {}, {}, path="request")
    _accepts(added, baseline, {}, {}, path="response")
    added["required"].append("label")
    with pytest.raises(AssertionError, match="requires a field"):
        _accepts(baseline, added, {}, {}, path="request")
    changed = deepcopy(baseline)
    changed["properties"]["id"]["type"] = "integer"
    with pytest.raises(AssertionError, match="changed wire type"):
        _accepts(changed, baseline, {}, {}, path="response")

    with pytest.raises(AssertionError, match="reference intersection"):
        _accepts({"type": "string"},
                 {"allOf": [{"type": "string"}], "enum": ["restricted"]}, {}, {}, path="ref")
    with pytest.raises(AssertionError, match="narrows open map"):
        _accepts(baseline, {**baseline, "additionalProperties": False}, {}, {}, path="map")
    with pytest.raises(AssertionError, match="intersecting property"):
        _variants({**baseline, "anyOf": [{"properties": {"id": {"type": "integer"}}}]})

    with pytest.raises(AssertionError, match="ambiguous oneOf"):
        _variants({"oneOf": [{"type": "string"}, {"type": "string", "enum": ["x"]}]})

    with pytest.raises(AssertionError, match="mixed reference combiners"):
        _accepts({"type": "string"},
                 {"$ref": "#/definitions/value", "allOf": [{"type": "integer"}]},
                 {}, {"value": {"type": "string"}}, path="ref")
    with pytest.raises(AssertionError, match="mixed union combiners"):
        _variants({"anyOf": [{"type": "string"}], "oneOf": [{"type": "integer"}]})
    with pytest.raises(AssertionError, match="ambiguous oneOf"):
        _variants({"oneOf": [{"type": "integer"}, {"type": "number"}]})
