"""Conservative selection rules, independent of Git and the filesystem."""
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from planner.environments.related_checks_contracts import SUITES, RelatedDomain, Suite


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def test_suite(path: str) -> Suite | None:
    for suite in ("unit", "integration", "e2e"):
        if path.startswith(f"tests/{suite}/") and path.endswith(".py"):
            return suite
    if path.startswith("web/tests/"):
        if path.endswith(".test.ts"):
            return "vitest"
        if path.endswith(".test.mjs"):
            return "browser"
    return None


def select_paths(
    changes: list[tuple[str, str]],
    inventory: dict[Suite, tuple[str, ...]],
    domains: tuple[RelatedDomain, ...],
    docs: tuple[str, ...],
    force_broad: str | None = None,
) -> tuple[dict[Suite, list[str]], bool, list[str]]:
    selected: dict[Suite, set[str]] = {suite: set() for suite in SUITES}
    reasons: list[str] = []
    broad = force_broad is not None
    if force_broad:
        reasons.append(force_broad)
    for status, path in changes:
        if matches(path, docs):
            continue
        if ("related_checks" in path or path.endswith("/conftest.py")
                or (path.startswith("tests/") and path.endswith(".py")
                    and not PurePosixPath(path).name.startswith("test_"))
                or matches(path, ("web/*.json", "web/*.ts", "web/*.js"))
                or matches(path, ("web/src/lib/api.ts", "web/src/lib/types.ts"))):
            broad = True
            reasons.append(f"{path}: selection, shared fixture, or wire contract change")
            continue
        groups = [domain for domain in domains if matches(path, domain.patterns)]
        suite = test_suite(path)
        if suite and (status == "D" or not groups):
            selected[suite].update(inventory[suite])
            reasons.append(f"{path}: deleted or unclassified test selects whole {suite} suite")
        elif groups:
            for domain in groups:
                reasons.append(f"{path}: {domain.name}")
                for kind in SUITES:
                    missing = set(domain.tests.get(kind, ())) - set(inventory[kind])
                    if missing:
                        broad = True
                        reasons.append(f"Missing mapped coverage: {', '.join(sorted(missing))}")
                    selected[kind].update(domain.tests.get(kind, ()))
        elif path.startswith(("web/src/", "assets/")):
            for kind in ("vitest", "browser", "e2e"):
                selected[kind].update(inventory[kind])
            reasons.append(f"{path}: unclassified frontend selects web and E2E")
        else:
            broad = True
            reasons.append(f"{path}: shared or unknown impact selects all default suites")
        # Every HTTP API change must retain authority protection, including a narrow domain.
        if path.startswith("src/planner/") and path.endswith("/api.py"):
            authority = "tests/unit/test_authority_matrix.py"
            if authority not in inventory["unit"]:
                broad = True
                reasons.append("Missing HTTP authority matrix; selecting all default suites")
            else:
                selected["unit"].add(authority)
    if broad:
        selected = {suite: set(inventory[suite]) for suite in SUITES}
    if not reasons:
        reasons.append("Documentation-only or empty release: candidate proof still required")
    return {suite: sorted(selected[suite]) for suite in SUITES}, broad, reasons
