"""Prove that a Ticket list arrives settled when the reader switches pages.

The Tickets section on a Manager or Chief page can draw from rows another surface
loaded, and then from count-only windows, before its own rows arrive. Each draw adds
or moves groups. That order depends on the live navigation stream, so frontend
fixtures cannot prove it. This proof records every draw while the sidebar switches
pages, and requires the first drawn list to be the final one.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.support.connected_rows import _propose
from tests.support.server_fixtures import rail_server as rail_server

OWNER = {"kind": "owner", "id": "owner"}

# Record each distinct state of the Tickets section, from first mount onward.
RECORD_DRAWS = """scope => {
  const draws = [];
  let last = '';
  const section = (root, filter) => root.querySelector(`[data-worker-section="${filter}"]`);
  const snapshot = () => {
    const root = document.querySelector(`[data-manager-children="${scope}"]`);
    if (!root) return null;
    const active = section(root, 'active');
    const count = filter =>
      section(root, filter)?.querySelector('[data-group-count]')?.textContent ?? null;
    return JSON.stringify({
      loading: active?.querySelector('[aria-busy="true"]') !== null,
      groups: [...(active?.querySelectorAll('details[data-workspace-group]') ?? [])].map(group => [
        group.dataset.workspaceGroup, group.querySelector('[data-group-count]')?.textContent,
        group.querySelectorAll('[data-worker-ticket-id]').length]),
      empty: active?.querySelector('.manager-workspace-empty')?.textContent ?? null,
      backlogged: count('backlogged'), done: count('done'),
    });
  };
  const observe = () => {
    const next = snapshot();
    if (next && next !== last) { draws.push(JSON.parse(next)); last = next; }
  };
  new MutationObserver(observe).observe(document.body,
    {subtree:true, childList:true, characterData:true, attributes:true});
  window.__ticketListDraws = draws;
  window.__quiet = {count: -1, since: 0};
}"""


def _ticket(
    api: ApiHelper, server: ServerHandle, manager: JsonObject, title: str, **extra: Any
) -> JsonObject:
    return api.direct_post(
        server,
        "/api/tickets",
        {
            "title": title,
            "worker_type": "coding",
            "brief": "Agreed brief.",
            "manager": manager,
            **extra,
        },
    )


def _finish(api: ApiHelper, server: ServerHandle, ticket: JsonObject) -> None:
    identity = str(ticket["id"])
    api.direct_post(
        server,
        f"/api/tickets/{identity}/accept/brief",
        {"next_ceiling": "done", "next_holder": OWNER},
    )
    # A Done ceiling accepts each proposal as it arrives.
    for field in ("what_changes", "implementation", "consequences"):
        _propose(server, identity, f"Synthetic {field}.")
    assert api.get(server, f"/api/tickets?detail=full&id={identity}")["stage"] == "done"


def _seed(api: ApiHelper, server: ServerHandle) -> tuple[JsonObject, JsonObject]:
    project = api.direct_post(server, "/api/projects", {"name": "First draw proof"})
    first = api.direct_post(
        server, "/api/managers", {"title": "Small manager", "project_id": project["id"]}
    )
    full = api.direct_post(
        server, "/api/managers", {"title": "Full manager", "project_id": project["id"]}
    )
    small = {"kind": "manager", "id": first["id"]}
    many = {"kind": "manager", "id": full["id"]}
    _ticket(api, server, small, "Only Worker on the small manager")
    blocker = _ticket(api, server, many, "Worker that blocks the others")
    for index in range(3):
        blocked = _ticket(
            api, server, many, f"Blocked Worker {index}", blocked_by_ticket_ids=[blocker["id"]]
        )
        api.direct_post(
            server,
            f"/api/tickets/{blocked['id']}/accept/brief",
            {"next_ceiling": "none", "next_holder": OWNER},
        )
    approval = _ticket(api, server, many, "Worker that needs approval")
    api.direct_post(
        server,
        f"/api/tickets/{approval['id']}/accept/brief",
        {"next_ceiling": "needs_what_changes", "next_holder": OWNER},
    )
    _propose(server, str(approval["id"]), "Synthetic what changes.")
    deferred = _ticket(api, server, many, "Backlogged Worker")
    api.direct_post(server, f"/api/tickets/{deferred['id']}/backlog", {})
    for index in range(2):
        _finish(api, server, _ticket(api, server, many, f"Finished Worker {index}"))
    chief = {"kind": "chief", "id": "chief"}
    for index in range(3):
        _ticket(api, server, chief, f"Chief Worker {index}")
    return first, full


def _switch(page: Page, scope: str, click: Callable[[], None]) -> list[dict[str, Any]]:
    page.evaluate(RECORD_DRAWS, scope)
    click()
    # Settled means no new draw for a full second after the list is on screen.
    page.wait_for_function(
        """() => {
          const draws = window.__ticketListDraws, last = draws.at(-1), quiet = window.__quiet;
          if (!last || last.loading || last.done === '') return false;
          if (quiet.count !== draws.length) {
            quiet.count = draws.length; quiet.since = performance.now();
          }
          return performance.now() - quiet.since > 1000;
        }""",
        polling=100,
        timeout=WAIT_MS,
    )
    draws: list[dict[str, Any]] = page.evaluate("window.__ticketListDraws")
    evidence = os.environ.get("PANELS_UI_PROOF_DIR")
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        name = scope.split(":")[0]
        (folder / f"ticket-list-draws-{name}.json").write_text(json.dumps(draws, indent=1))
        page.screenshot(path=str(folder / f"ticket-list-{name}.png"))
    return draws


def _assert_settled_on_arrival(draws: list[dict[str, Any]], label: str) -> None:
    drawn = [draw for draw in draws if not draw["loading"]]
    assert drawn, (label, draws)
    final = drawn[-1]
    # Before the list arrives, history counts are blank or already final.
    for draw in draws[: draws.index(drawn[0])]:
        assert draw["backlogged"] in ("", final["backlogged"]), (label, draws)
        assert draw["done"] in ("", final["done"]), (label, draws)
    for key in ("groups", "empty", "backlogged", "done"):
        assert all(draw[key] == final[key] for draw in drawn), (label, key, draws)


def test_ticket_lists_arrive_settled_when_switching_pages(
    rail_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    first, full = _seed(api, server)
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/item/{first['id']}",
        f'[data-manager-children="manager:{first["id"]}"]',
    )
    page.set_default_timeout(WAIT_MS)
    rail = page.locator(".active-work-scroll")

    draws = _switch(
        page,
        f"manager:{full['id']}",
        lambda: rail.locator(f'.manager-card[data-manager="{full["id"]}"] .item-head').click(),
    )
    _assert_settled_on_arrival(draws, "manager")
    final = draws[-1]
    groups = {key: (count, rows) for key, count, rows in final["groups"]}
    assert groups["blocked"] == ("3", 3), final
    assert groups["awaiting_approval"] == ("2", 2), final
    assert (final["backlogged"], final["done"]) == ("1", "2"), final

    draws = _switch(
        page,
        "chief:chief",
        lambda: rail.locator("[data-chief-destination]").click(),
    )
    _assert_settled_on_arrival(draws, "chief")
    assert sum(rows for _key, _count, rows in draws[-1]["groups"]) == 3, draws[-1]
