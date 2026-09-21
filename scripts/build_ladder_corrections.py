"""Build the frozen correction data for the stage-ladder revision.

The revision before this one may replace only text that occurs verbatim in a version
this repository shipped. That proof is what stops a revision deleting the owner's
writing, and it stays exactly as it is. It cannot cover these passages: they are the
owner's own wording, and no shipped version contains them.

So the proof here is different, and narrower. Every correction renames a Stage or a
field inside a sentence the owner keeps, the whole set was read and approved by the
owner as exact before-and-after text, and this script refuses any span that does not
occur exactly once in the row it names. Run it by hand against a copy of the live
database, read what it prints, and commit the generated module.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REHEARSAL_DB = sys.argv[1]
OUT = Path("src/planner/core/migrations/skill_ladder_correction_data.py")

# (skill name, exact text to replace, replacement, why). Approved as exact text by the
# owner on Ticket t_b42uq02f, which is the authority these corrections run on.
CORRECTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "panels",
        "Ticket workers shape a Ticket through **Kickoff → Success → Approach → Plan →\n"
        "Implementation → Closeout → Done**",
        "Ticket workers shape a Ticket through **Brief → Success Condition → What Changes → Plan →\n"
        "Implementation → Consequences → Done**",
        "the coding ladder, in the first skill every agent loads",
    ),
    (
        "panels",
        "Kickoff, success, approach, plan, implementation, closeout, recaps, and notes",
        "Brief, success condition, what changes, plan, implementation, consequences, recaps, and notes",
        "the same six fields, named again in prose",
    ),
    (
        "panels-worker-initiative-planning",
        "The sequence is **Kickoff → Rough Shape → Question Tree → Question Answers →"
        " Ticket Outlines → Closeout → Done**.",
        "The sequence is **Brief → Rough Split into Parts → Question Tree → Question Answers →"
        " Proposed Tickets → Consequences → Done**.",
        "the initiative-planning ladder",
    ),
    (
        "panels-worker-initiative-planning",
        "A good **kickoff** lets the Rough Shape begin",
        "A good **Brief** lets the Rough Split into Parts begin",
        "two field names in one sentence",
    ),
    (
        "panels-worker-initiative-planning",
        "Build a prioritized, nested tree under the Rough Shape's parts.",
        "Build a prioritized, nested tree under the Rough Split into Parts.",
        "field name",
    ),
    (
        "panels-worker-initiative-planning",
        "group the settled answers by the Rough Shape,",
        "group the settled answers by the Rough Split into Parts,",
        "field name",
    ),
    (
        "panels-worker-initiative-planning",
        "Check the package against the Rough Shape.",
        "Check the package against the Rough Split into Parts.",
        "field name",
    ),
    (
        "panels-worker-initiative-planning",
        "- Keep the sequence honest: Rough Shape identifies parts; Question Tree exposes shared"
        " decisions; Question Answers settles them; Ticket Outlines derives the work; Closeout"
        " creates it.",
        "- Keep the sequence honest: Rough Split into Parts identifies parts; Question Tree exposes"
        " shared decisions; Question Answers settles them; Proposed Tickets derives the work;"
        " Consequences creates it.",
        "the ladder a second time, in prose",
    ),
    (
        "panels-chief-of-staff",
        "(for `coding`: `success`, `approach`, `plan`,\n`implementation`, `closeout`;"
        " other Worker types have their own)",
        "(for `coding`: `success_condition`, `what_changes`, `plan`,\n`implementation`,"
        " `consequences`; other Worker types have their own)",
        "three of five gated field names",
    ),
    (
        "panels-worker",
        "link them from the relevant proposal, note, implementation, or closeout as",
        "link them from the relevant proposal, note, implementation, or consequences as",
        "field name",
    ),
    (
        "panels-worker-coding",
        "  - What will be done in closeout.",
        "  - What will be done in Consequences.",
        "field name",
    ),
    (
        "panels-worker-coding",
        "cleanup route before proposing **closeout**.",
        "cleanup route before proposing **Consequences**.",
        "field name",
    ),
    (
        "panels-worker-coding",
        "A good **closeout** is a short, verified report of success and how it was checked.",
        "A good **Consequences** is a short, verified report of success and how it was checked.",
        "field name",
    ),
    (
        "panels-worker-planning-sprint",
        "Closeout is the only canonical-write phase.",
        "Consequences is the only canonical-write phase.",
        "field name",
    ),
)

# What must be absent from these rows once this revision has run. A name that is still
# here is this revision failing silently, which is the bug the revision before it exists
# to prevent. Only names that no longer exist anywhere in the build are claimed. The
# words "approach" and "kickoff" are not claimed: "approach" is ordinary English, and
# "kickoff" names the live `--kickoff-note` option and the sprint kickoff document.
CLAIMS: tuple[tuple[str, str], ...] = (
    ("panels", "Kickoff"),
    ("panels", "Approach"),
    ("panels", "Closeout"),
    ("panels-worker-initiative-planning", "Kickoff"),
    ("panels-worker-initiative-planning", "Rough Shape"),
    ("panels-worker-initiative-planning", "Ticket Outlines"),
    ("panels-worker-initiative-planning", "Closeout"),
    ("panels-chief-of-staff", "`approach`"),
    ("panels-chief-of-staff", "`closeout`"),
    ("panels-worker", "or closeout"),
    ("panels-worker-coding", "closeout"),
    ("panels-worker-planning-sprint", "Closeout"),
)


def main() -> None:
    conn = sqlite3.connect(REHEARSAL_DB)
    rows = {
        str(name): str(text)
        for name, text in conn.execute("SELECT skill_name, source_text FROM managed_skills")
    }

    failed = False
    corrected = dict(rows)
    for skill, old, new, why in CORRECTIONS:
        occurrences = rows.get(skill, "").count(old)
        if occurrences != 1:
            print(f"REFUSED  {skill}: span occurs {occurrences} times, not once — {why}")
            failed = True
            continue
        corrected[skill] = corrected[skill].replace(old, new)
        print(f"exact    {skill:34} {len(old):4} chars  {why}")
    if failed:
        raise SystemExit("some spans are not exact; nothing written")

    for skill, must_be_absent in CLAIMS:
        if must_be_absent in corrected[skill]:
            print(f"UNMET    {skill} still names {must_be_absent!r}")
            failed = True
    if failed:
        raise SystemExit("some claims are unmet; nothing written")

    body = [
        '"""The exact text the stage-ladder revision replaces, and the authority for it.',
        "",
        "Every entry renames a Stage or a field inside a sentence the owner keeps. The owner",
        "read and approved the whole set as exact before-and-after text on Ticket t_b42uq02f.",
        "Generated by scripts/build_ladder_corrections.py and frozen: a revision records what",
        "happened.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        "# (skill name, exact text to replace, replacement, why)",
        "LADDER_CORRECTIONS: Final[tuple[tuple[str, str, str, str], ...]] = (",
    ]
    for skill, old, new, why in CORRECTIONS:
        body.append("    (")
        body.append(f"        {skill!r},")
        body.append(f"        {old!r},")
        body.append(f"        {new!r},")
        body.append(f"        {why!r},")
        body.append("    ),")
    body.append(")")
    body.append("")
    body.append("# What must be absent from these rows once this revision has run. Anything still")
    body.append("# here is this revision failing silently.")
    body.append("LADDER_CLAIMS: Final[tuple[tuple[str, str], ...]] = (")
    for claim in CLAIMS:
        body.append(f"    {claim!r},")
    body.append(")")
    OUT.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT} with {len(CORRECTIONS)} corrections and {len(CLAIMS)} claims")


if __name__ == "__main__":
    main()
