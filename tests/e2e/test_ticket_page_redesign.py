"""Browser contract for the redesigned single-ticket page."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def test_ticket_masthead_identity_recap_and_real_wrapping(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    sprint = api.direct_post(
        server,
        "/api/sprints",
        {
            "name": "A deliberately descriptive later sprint",
            "date_start": "2026-08-10",
            "date_end": "2026-08-23",
        },
    )
    cli(server, "project", "create", "--name", "Panels", "--priority", "P2")
    item_id = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Redesigned ticket page",
        "--project",
        "Panels",
        "--sprint",
        sprint["id"],
    )["id"]
    empty_ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Identity facts are initially empty",
    )["id"]
    empty_ready = f'section[data-screen="ticket"][data-ticket-id="{empty_ticket_id}"]'
    page = open_page(context_factory(), server, f"#/ticket/{empty_ticket_id}", empty_ready)
    assert page.locator("[data-ticket-identity] .ticket-identity-add").count() == 3
    assert page.locator('[data-priority-control] [data-priority-tile="P3"]').count() == 1

    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "A title which owns its own row",
    )["id"]
    blocker_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "A long blocker title that must stay inside the narrow ticket masthead",
    )["id"]
    cli(server, "ticket", "block", ticket_id, "--by", blocker_id)
    api.direct_patch(
        server,
        f"/api/tickets/{ticket_id}",
        {"priority": "P0", "deadline": "2026-08-12"},
    )
    api.direct_post(server, f"/api/items/{item_id}/tickets", {"ticket_id": ticket_id})

    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page.goto(f"{server.base}/#/ticket/{ticket_id}")
    page.wait_for_selector(ready, timeout=WAIT_MS)

    identity = page.locator("[data-ticket-identity]")
    assert "P0" in identity.inner_text()
    assert "sprint" in identity.inner_text().lower()
    assert "A DELIBERATELY DESCRIPTIVE LATER SPRINT" in identity.inner_text()
    assert "due" in identity.inner_text().lower()
    assert "AUG 12" in identity.inner_text()
    assert page.locator("[data-project-control]").count() == 0
    assert page.locator(".ticket-priority-alert").count() == 0
    assert page.locator(".ticket-status-display, [data-ticket-status]").count() == 0
    assert page.locator(".ticket-planning").count() == 0

    priority_colours = page.locator('[data-priority-tile="P0"]').evaluate(
        """element => {
          const style = getComputedStyle(element);
          const tokens = getComputedStyle(document.documentElement);
          const probe = document.createElement("span");
          probe.style.color = tokens.getPropertyValue("--priority-p0-ink").trim();
          probe.style.backgroundColor = tokens.getPropertyValue("--priority-p0-fill").trim();
          document.body.appendChild(probe);
          const expected = getComputedStyle(probe);
          const result = {
            color: style.color,
            background: style.backgroundColor,
            expectedColor: expected.color,
            expectedBackground: expected.backgroundColor,
          };
          probe.remove();
          return result;
        }"""
    )
    assert priority_colours["color"] == priority_colours["expectedColor"]
    assert priority_colours["background"] == priority_colours["expectedBackground"]

    order = page.evaluate(
        """() => {
          const identity = document.querySelector("[data-ticket-identity]");
          const title = document.querySelector(".ticket-title-row");
          const operating = document.querySelector(".ticket-operating");
          return [identity, title, operating].map(element =>
            Math.round(element.getBoundingClientRect().top)
          );
        }"""
    )
    assert order == sorted(order)
    assert len(set(order)) == 3
    assert page.locator(".ticket-title-row [data-copy]").count() == 0
    assert page.locator(".ticket-operating [data-copy]").count() == 1
    assert page.locator(f'[data-blocker-chip="{blocker_id}"]').count() == 1

    priority_control = page.locator("[data-priority-control]")
    page.locator("[data-priority-control] select").focus()
    focus_state = priority_control.evaluate(
        """element => ({
          focusedWithin: element.matches(":focus-within"),
          outline: getComputedStyle(element).outlineStyle,
        })"""
    )
    assert focus_state == {"focusedWithin": True, "outline": "solid"}

    recap = page.locator("[data-recap]")
    assert recap.locator("details").count() == 0
    assert recap.locator("[data-markdown-inline-edit]").count() == 1
    assert "Recap" not in recap.inner_text()
    assert recap.locator(".ticket-recap-inner").evaluate(
        """element => {
          const expected = getComputedStyle(document.documentElement)
            .getPropertyValue("--surface-sunken").trim();
          const probe = document.createElement("span");
          probe.style.backgroundColor = expected;
          document.body.appendChild(probe);
          const expectedRgb = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return getComputedStyle(element).backgroundColor === expectedRgb;
        }"""
    )

    page.set_viewport_size({"width": 320, "height": 800})
    geometry = page.evaluate(
        """() => {
          const identity = document.querySelector("[data-ticket-identity]");
          const operating = document.querySelector(".ticket-operating");
          const title = document.querySelector(".ticket-title-row");
          const actionGroup = document.querySelector(".ticket-operating-actions");
          const leash = document.querySelector(".ticket-leash");
          const ticketDoc = document.querySelector(".ticket-doc");
          const blockers = document.querySelector("[data-blocker-summary]");
          const tops = element => [...element.children].map(child =>
            Math.round(child.getBoundingClientRect().top)
          );
          return {
            identityLines: new Set(tops(identity)).size,
            operatingLines: new Set(tops(operating)).size,
            titleBottom: title.getBoundingClientRect().bottom,
            operatingTop: operating.getBoundingClientRect().top,
            actionTop: actionGroup.getBoundingClientRect().top,
            leashTop: leash.getBoundingClientRect().top,
            overflow: document.documentElement.scrollWidth - window.innerWidth,
            ticketDocOverflow: ticketDoc.scrollWidth - ticketDoc.clientWidth,
            blockerOverflow: blockers.scrollWidth - blockers.clientWidth,
          };
        }"""
    )
    assert geometry["identityLines"] > 1
    assert geometry["operatingLines"] > 1
    assert geometry["titleBottom"] <= geometry["operatingTop"]
    assert geometry["actionTop"] > geometry["leashTop"]
    assert geometry["overflow"] <= 0
    assert geometry["ticketDocOverflow"] <= 0
    assert geometry["blockerOverflow"] <= 0


def test_current_stage_only_labels_ambiguous_user_and_approval_states(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Stage summary cues",
    )["id"]
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/ticket/{ticket_id}", ready)

    assert page.locator("[data-stage-run-label]").count() == 0
    page.click("[data-ticket-takeover-toggle]")
    page.wait_for_selector(
        '[data-stage-run-label="you\'re on it"] [data-stage-release]',
        timeout=WAIT_MS,
    )
    assert page.locator("[data-stage-run-label]").count() == 1
    page.set_viewport_size({"width": 320, "height": 800})
    cue_geometry = page.locator(
        '[data-stage-run-label="you\'re on it"]'
    ).evaluate(
        """element => {
          const summary = element.closest(".disclosure-summary");
          const ticketDoc = document.querySelector(".ticket-doc");
          return {
            summaryOverflow: summary.scrollWidth - summary.clientWidth,
            ticketDocOverflow: ticketDoc.scrollWidth - ticketDoc.clientWidth,
          };
        }"""
    )
    assert cue_geometry["summaryOverflow"] <= 0
    assert cue_geometry["ticketDocOverflow"] <= 0

    page.click("[data-stage-release]")
    page.wait_for_selector("[data-stage-run-label]", state="detached", timeout=WAIT_MS)
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success is ready.",
        ticket_id=ticket_id,
        stdin="Success proposal body.",
    )
    page.wait_for_selector(
        '[data-stage-run-label="awaiting approval"]',
        timeout=WAIT_MS,
    )
    approval_release = page.locator(
        '[data-stage-run-label="awaiting approval"] [data-stage-release]'
    )
    assert approval_release.count() == 0
    assert page.locator("[data-stage-run-label]").count() == 1
