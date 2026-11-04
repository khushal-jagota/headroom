"""Fixed goal-projection rules over supplied definitions and retained evidence."""

from planner.measures.contracts import Measure, MeasureDate, MeasureReadStatus, MeasureValue
from planner.measures.logic.dates import invalid
from planner.measures.logic.evaluation import Evaluation, demanded_pairs


def check_goal_demand_count(count: int, *, demand_limit: int) -> None:
    if count > demand_limit:
        raise invalid(
            f"goal projection exceeds {demand_limit} demanded "
            "measure/date pairs; request a narrower date range"
        ) from None


def goal_demanded_pairs(
    roots: tuple[MeasureDate, ...],
    definitions: dict[str, Measure],
    *,
    now: int,
    demand_limit: int,
) -> set[MeasureDate]:
    """Recover independent goal roots; ordinary measure reads retain their own policy."""
    try:
        return demanded_pairs(
            roots, definitions, now=now, limit=demand_limit, missing_ok=True
        )
    except RecursionError:
        pairs: set[MeasureDate] = set()
        for root in roots:
            try:
                pairs.update(
                    demanded_pairs(
                        (root,), definitions, now=now, limit=demand_limit, missing_ok=True
                    )
                )
            except RecursionError:
                continue
            check_goal_demand_count(len(pairs), demand_limit=demand_limit)
        return pairs


def goal_actual_value(
    definition: Measure,
    pair: MeasureDate,
    evaluation: Evaluation,
    *,
    today: str,
    has_retained_evidence: bool,
) -> MeasureValue:
    """The caller selects observation membership or recorded-date membership."""
    if pair.date > today and not has_retained_evidence:
        return MeasureValue(
            definition.id,
            pair.date,
            definition.definition_revision,
            MeasureReadStatus.missing_input,
            None,
            (),
            (pair,),
        )
    return evaluation.measure(pair)
