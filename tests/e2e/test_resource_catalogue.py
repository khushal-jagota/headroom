"""Browser proof for the Resource Catalogue's two refresh signals and precision."""

from __future__ import annotations

import json
import sqlite3

from playwright.sync_api import Page, WebSocketRoute

WAIT_MS = 10_000


def _count(requests: list[tuple[str, str]], server, path: str) -> int:
    return requests.count(("GET", server.base + path))


def _watch_requests(page: Page, requests: list[tuple[str, str]]) -> None:
    page.on("request", lambda request: requests.append((request.method, request.url)))


def _open_without_event_delivery(context_factory, server, route: str, ready: str) -> Page:
    page = context_factory().new_page()

    def keep_socket_inert(_socket: WebSocketRoute) -> None:
        # The browser owns a WebSocket, but this route neither connects it to the
        # server nor sends messages. Successful writes therefore have no event help.
        return

    page.route_web_socket("**/api/events*", keep_socket_inert)
    page.goto(server.base + "/" + route)
    page.wait_for_selector(ready, timeout=WAIT_MS)
    return page


def _wait_for_flush(page: Page, previous: int) -> None:
    page.wait_for_function(
        "previous => window.__plannerDebug.flushes > previous",
        arg=previous,
        timeout=WAIT_MS,
    )


def _append_event(server, entity_id: str, kind: str, payload: dict | None = None) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, 10)",
            (entity_id, kind, json.dumps(payload or {})),
        )


def _park_success_proposal(server, cli, api, title: str) -> str:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        title,
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        f"{title} recap",
        ticket_id=ticket_id,
        stdin=f"{title} proposal",
    )
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET employee_session_id = ? WHERE id = ?",
            (f"catalogue-session-{ticket_id}", ticket_id),
        )
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})
    return ticket_id


def test_successful_and_failed_idea_mutations_refresh_only_after_success_without_socket(
    server, context_factory
) -> None:
    requests: list[tuple[str, str]] = []
    page = _open_without_event_delivery(
        context_factory, server, "#/ideas", '[data-screen="ideas"] .capture'
    )
    _watch_requests(page, requests)
    page.wait_for_selector('[data-screen="ideas"] [data-commit]', timeout=WAIT_MS)

    fail_next = True

    def maybe_fail(route) -> None:
        nonlocal fail_next
        if route.request.method == "POST" and fail_next:
            fail_next = False
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"error": {"code": "test", "message": "capture failed"}}),
            )
            return
        route.continue_()

    page.route("**/api/ideas", maybe_fail)
    requests.clear()
    page.fill('[data-create="idea"] [data-input="title"]', "Rejected idea")
    page.click('[data-create="idea"] [data-commit]')
    page.locator(".error-line", has_text="capture failed").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert _count(requests, server, "/api/ideas") == 0
    assert _count(requests, server, "/api/projects") == 0

    requests.clear()
    page.fill('[data-create="idea"] [data-input="title"]', "Immediate catalogue idea")
    page.click('[data-create="idea"] [data-commit]')
    page.locator("[data-ideas]", has_text="Immediate catalogue idea").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert _count(requests, server, "/api/ideas") == 1
    assert _count(requests, server, "/api/projects") == 0


def test_review_subscribers_share_one_read_and_each_advance_awaits_one_refresh(
    server, context_factory, cli, api
) -> None:
    first = _park_success_proposal(server, cli, api, "Catalogue review first")
    second = _park_success_proposal(server, cli, api, "Catalogue review second")
    requests: list[tuple[str, str]] = []
    page = context_factory().new_page()
    page.route_web_socket("**/api/events*", lambda _socket: None)
    _watch_requests(page, requests)
    page.goto(server.base + "/#/review")
    page.wait_for_selector("[data-review-card]", timeout=WAIT_MS)
    assert _count(requests, server, "/api/review") == 1

    current = page.locator("[data-review-card]").get_attribute("data-ticket-id")
    assert current in {first, second}
    remaining = second if current == first else first
    review_reads = _count(requests, server, "/api/review")
    ticket_reads = _count(requests, server, f"/api/tickets/{current}")
    page.wait_for_function(
        "() => { const button = document.querySelector('[data-review-card] [data-accept]');"
        " return button && !button.disabled; }",
        timeout=WAIT_MS,
    )
    page.click("[data-review-card] [data-accept]")
    page.wait_for_selector(
        f'[data-review-card][data-ticket-id="{remaining}"]', timeout=WAIT_MS
    )
    assert _count(requests, server, "/api/review") == review_reads + 1
    assert _count(requests, server, f"/api/tickets/{current}") == ticket_reads + 1

    review_reads = _count(requests, server, "/api/review")
    page.fill("[data-review-card] [data-review-revision-input]", "Please revise exactly once.")
    page.click("[data-review-card] [data-review-revision-send]")
    page.wait_for_selector(
        f'[data-review-card][data-ticket-id="{remaining}"]',
        state="detached",
        timeout=WAIT_MS,
    )
    assert _count(requests, server, "/api/review") == review_reads + 1


def test_project_name_event_refetches_only_subscribed_aggregate_set_and_matching_ticket(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Project-name catalogue ticket",
        "--project-id",
        "project_tribe",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    requests: list[tuple[str, str]] = []
    page_specs = [
        ("#/workspace", 'section[data-screen="workspace"]'),
        ("#/day", 'section[data-screen="day"]'),
        ("#/backlog", 'section[data-screen="backlog"]'),
        ("#/ideas", 'section[data-screen="ideas"]'),
        ("#/sprint", 'section[data-screen="sprint"]'),
        (
            f"#/ticket/{ticket_id}",
            f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        ),
    ]
    pages: list[Page] = []
    for route, ready in page_specs:
        page = open_page(context_factory(), server, route, ready, settled=True)
        _watch_requests(page, requests)
        pages.append(page)

    def mutate_and_wait(path: str, body: dict) -> None:
        before = [page.evaluate("window.__plannerDebug.flushes") for page in pages]
        requests.clear()
        api.direct_patch(server, path, body)
        for page, previous in zip(pages, before, strict=True):
            _wait_for_flush(page, previous)

    observed_paths = [
        "/api/board",
        "/api/day/today",
        "/api/items?sprint_id=null",
        "/api/ideas",
        "/api/projects",
        "/api/sprint/current",
        f"/api/tickets/{ticket_id}",
        "/api/review",
        "/api/sprints",
        "/api/worker-types",
    ]

    mutate_and_wait("/api/projects/project_tribe", {"summary": "Summary only"})
    assert _count(requests, server, "/api/projects") == 3
    for path in observed_paths:
        if path != "/api/projects":
            assert _count(requests, server, path) == 0, path

    mutate_and_wait("/api/projects/project_tribe", {"name": "Tribe renamed"})
    assert _count(requests, server, "/api/board") == 1
    assert _count(requests, server, "/api/day/today") == 1
    assert _count(requests, server, "/api/items?sprint_id=null") == 1
    assert _count(requests, server, "/api/ideas") == 1
    assert _count(requests, server, "/api/projects") == 3
    assert _count(requests, server, "/api/sprint/current") == 2
    assert _count(requests, server, f"/api/tickets/{ticket_id}") == 1
    for path in [
        "/api/review",
        "/api/sprints",
        "/api/worker-types",
    ]:
        assert _count(requests, server, path) == 0, path

    mutate_and_wait("/api/projects/project_other", {"name": "Other renamed"})
    assert _count(requests, server, "/api/projects") == 3
    assert _count(requests, server, "/api/sprint/current") == 2
    assert _count(requests, server, f"/api/tickets/{ticket_id}") == 0


def test_ticket_employee_session_and_step_events_have_exact_network_dependencies(
    server, context_factory, open_page, cli
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Employee event catalogue ticket",
    )["id"]
    requests: list[tuple[str, str]] = []
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=True,
    )
    _watch_requests(page, requests)

    def emit(kind: str) -> None:
        previous = page.evaluate("window.__plannerDebug.flushes")
        requests.clear()
        _append_event(server, ticket_id, kind)
        _wait_for_flush(page, previous)

    emit("employee_session_changed")
    assert _count(requests, server, f"/api/tickets/{ticket_id}") == 1
    assert _count(requests, server, "/api/sprint/current") == 0
    assert _count(requests, server, "/api/review") == 0

    emit("employee_step_started")
    assert _count(requests, server, f"/api/tickets/{ticket_id}") == 1
    assert _count(requests, server, "/api/sprint/current") == 1
    assert _count(requests, server, "/api/review") == 0

    previous = page.evaluate("window.__plannerDebug.flushes")
    requests.clear()
    _append_event(server, "idea_unrelated", "idea_created")
    _wait_for_flush(page, previous)
    assert _count(requests, server, f"/api/tickets/{ticket_id}") == 0
