"""Guidance conversion retains every source byte and associates recognized stage text."""

from dataclasses import replace

from tests.support.probe import PROBE_WORKER_TYPE_DEFINITION

from planner.worker_types.guidance import (
    convert_existing_skill,
    guidance_conversion_report,
    render_specialist_skill,
    render_workflow_guide,
)


def test_conversion_accounts_for_exact_prose_and_stage_instructions() -> None:
    definition = PROBE_WORKER_TYPE_DEFINITION
    stage = definition.stages[0]
    body = (
        f"# Owner instructions\n\nKeep every byte.\n\n## {stage.label}\n\n"
        "Do exactly this.\n\n# Shared rules\n\nKeep these too.\n"
    )
    source = f"---\nname: probe-worker\ndescription: Probe\n---\n{body}"
    report = guidance_conversion_report(definition, source)
    assert "".join(fragment["text"] for fragment in report["fragments"]) == body
    assert report["preserved_body_bytes"] == len(body.encode())
    converted = convert_existing_skill(definition, source)
    assert "Do exactly this." in converted.stages[0].instructions
    assert "Do exactly this." not in converted.guidance
    assert "Keep every byte." in converted.guidance
    assert "Keep these too." in converted.guidance
    assert all(
        fragment["text"] in converted.guidance
        or any(fragment["text"] in item.instructions for item in converted.stages)
        for fragment in report["fragments"]
    )
    guide = render_workflow_guide(converted)
    assert guide in render_specialist_skill(converted, description="Probe")
    changed = replace(
        converted,
        stages=tuple(
            replace(item, instructions="Updated") if item.id == stage.id else item
            for item in converted.stages
        ),
    )
    assert "Do exactly this." not in render_workflow_guide(changed)


def test_stage_subtitles_nested_headings_and_fenced_examples_keep_their_owner() -> None:
    definition = PROBE_WORKER_TYPE_DEFINITION
    first, second = definition.stages[:2]
    for separator in ("—", "–", "-"):
        escaped_id = first.id.replace("_", "\\_")
        body = (
            f"# Shared rules\n\n### {escaped_id} {separator} write the artifacts\n\n"
            "Actual instructions.\n\n#### Evidence\n\nKeep nested detail in this stage.\n\n"
            f"```markdown\n### {second.id} — this is only an example\n```\n\n"
            "~~~markdown\n### Shared-looking example\n~~~\n\n"
            "Keep text after the example in this stage.\n\n"
            "### Working disciplines\n\nThis applies to every stage.\n"
            f"### {first.id}ly — not the stage\n\nThis is unmatched prose.\n"
        )
        source = f"---\nname: probe-worker\ndescription: Probe\n---\n{body}"
        report = guidance_conversion_report(definition, source)
        assert "".join(fragment["text"] for fragment in report["fragments"]) == body
        converted = convert_existing_skill(definition, source)
        instructions = converted.stages[0].instructions
        assert "Actual instructions." in instructions
        assert "Keep nested detail in this stage." in instructions
        assert "this is only an example" in instructions
        assert "Keep text after the example in this stage." in instructions
        assert "This applies to every stage." not in instructions
        assert "This applies to every stage." in converted.guidance
        assert "This is unmatched prose." in converted.guidance
        assert not converted.stages[1].instructions
