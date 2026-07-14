"""The canonical ``probe`` ticket-type fixture — the genericity proof's reusable shape.

``probe`` is a synthetic SECOND workflow definition, deliberately a different shape
from ``coding``, registered TEST-ONLY (never in production ``coding_registry()``).
It exists so the persistence + engine stack can be driven with a NON-coding type,
proving the machine is genuinely N-ary and not ``coding`` in disguise. This module
is the single source of the probe shape: t_tt02b (generic field storage), t_tt03
(the go/no-go gate), and t_tt05 (worker realization) all import it rather than
redeclaring their own inline probe.

Shape (contrasting coding at every free choice):
- Stages: ``needs_kickoff`` (gates ``kickoff``) -> ``needs_alpha`` (gates ``alpha``)
  -> ``needs_beta`` (gates ``beta``) -> ``done``; ``dropped`` is the reserved
  exceptional terminal. It shares only the universal ``needs_kickoff``/``kickoff``
  prefix and the ``done`` bookend; the middle stages/fields (``needs_alpha``/``alpha``,
  ``needs_beta``/``beta``) are NOT ``CodingStage``/``FieldName`` members — proving the
  engine is not enum-bound.
- Fields (ordered): ``kickoff``, ``alpha``, ``beta``.
- Ceiling range: ``needs_alpha`` -> ``needs_beta`` -> ``done``; default ceiling
  ``needs_alpha`` (its FIRST worker stage, NOT ``needs_success``) — this alone proves
  per-type default-ceiling derivation.
- ``supports_prefix_reconciliation``: ``True`` (consumed by t_tt03 external-work).
- One transition hook: ``needs_alpha`` -> ``needs_beta`` under ``khushal`` maps to a
  durable ``user_takeover`` — so the non-None transition-effect branch is exercised
  for a FOREIGN type (it is load-bearing for t_tt02b's drive).
- Worker profile: a PLACEHOLDER — a placeholder specialist skill / ``"default"``
  toolset (both added to the test catalogs below), ``model``/``reasoning_effort``
  None. Inert until t_tt05; validated here only for reference integrity.

The middle stage/field ids are built with ``"".join((...))`` so CPython string
interning cannot mask a surviving identity (``is``) comparison anywhere in the
engine; an adjacent-literal ``+`` would be constant-folded and interned.
"""

from __future__ import annotations

from planner.ticket_types.coding import CODING_DEFINITION
from planner.ticket_types.contracts import (
    FieldDef,
    Stage,
    TransitionHook,
    WorkerProfile,
    WorkflowDefinition,
)
from planner.ticket_types.registry import Registry, build_registry
from planner.tickets.contracts import Implementer, TicketStatus
from planner.tickets.logic import coding_bridge

# Non-enum stage/field ids, built so interning cannot hide an identity comparison.
NEEDS_ALPHA = "".join(("needs_", "alpha"))
NEEDS_BETA = "".join(("needs_", "beta"))
FIELD_ALPHA = "".join(("al", "pha"))
FIELD_BETA = "".join(("be", "ta"))

# The placeholder specialist skill the probe worker profile references. Inert until
# t_tt05; it is added to the test catalogs so R14 reference validation passes.
PROBE_SPECIALIST_SKILL = "probe-worker"

# The declared, ordered probe field ids (kickoff, alpha, beta).
PROBE_FIELD_IDS: tuple[str, ...] = ("kickoff", FIELD_ALPHA, FIELD_BETA)

PROBE_DEFINITION: WorkflowDefinition = WorkflowDefinition(
    type_id="probe",
    label="Probe",
    stages=(
        Stage(id="needs_kickoff", label="Kickoff", gating_field="kickoff", is_terminal=False),
        Stage(id=NEEDS_ALPHA, label="Alpha", gating_field=FIELD_ALPHA, is_terminal=False),
        Stage(id=NEEDS_BETA, label="Beta", gating_field=FIELD_BETA, is_terminal=False),
        Stage(id="done", label="Done", gating_field=None, is_terminal=True),
    ),
    dropped_stage=Stage(id="dropped", label="Dropped", gating_field=None, is_terminal=True),
    fields=(
        FieldDef(id="kickoff", label="Kickoff"),
        FieldDef(id=FIELD_ALPHA, label="Alpha"),
        FieldDef(id=FIELD_BETA, label="Beta"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill=PROBE_SPECIALIST_SKILL,
        model=None,
        reasoning_effort=None,
        toolset_profile="default",
    ),
    # needs_alpha -> needs_beta under khushal -> a durable user_takeover, so the
    # non-None transition-effect branch runs for a FOREIGN type.
    transition_hooks=(
        TransitionHook(
            old_stage=NEEDS_ALPHA,
            new_stage=NEEDS_BETA,
            implementer=Implementer.khushal.value,
            effect=TicketStatus.user_takeover.value,
        ),
    ),
    supports_prefix_reconciliation=True,
)

# The test catalogs the registry validator needs (R14/R15): coding's base + specialist
# skills plus the probe placeholder, and the shared "default" toolset profile. The
# registry validates coding's specialist too (build_probe_registry builds coding + probe),
# so "panels-worker-coding" must be present.
PROBE_KNOWN_SKILLS: frozenset[str] = frozenset(
    {"panels-worker", "panels-worker-coding", PROBE_SPECIALIST_SKILL}
)
PROBE_KNOWN_TOOLSET_PROFILES: frozenset[str] = frozenset({"default"})


def build_probe_registry() -> Registry:
    """A validated registry carrying coding + probe (production stays coding-only).

    The persistence/engine doors resolve a row's type through this when a test
    installs it via :func:`install_probe_registry`."""
    return build_registry(
        [CODING_DEFINITION, PROBE_DEFINITION],
        known_skills=PROBE_KNOWN_SKILLS,
        known_toolset_profiles=PROBE_KNOWN_TOOLSET_PROFILES,
    )


def install_probe_registry() -> WorkflowDefinition:
    """Install the coding+probe registry as the active (test) registry and return the
    resolved ``probe`` definition. Pair with :func:`uninstall_probe_registry` (a test
    fixture's teardown) to restore production coding-only afterward."""
    coding_bridge.set_registry_for_test(build_probe_registry())
    return coding_bridge.require("probe")


def uninstall_probe_registry() -> None:
    """Clear the test registry, restoring the production coding-only singleton."""
    coding_bridge.set_registry_for_test(None)
