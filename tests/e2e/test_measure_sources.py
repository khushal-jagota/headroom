"""One browser → credential → stored script/HTTP → stored value → SSE proof.

The UI password lifecycle and mounted completion cannot be proven by API or
component tests alone. Collection and repair use the production runtime.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import Browser, expect
from tests.e2e.measure_sources_server import FIRST_TOKEN, SECOND_TOKEN
from tests.support.server import ServerHandle

WAIT = 15000


def test_source_credentials_collection_recovery_and_second_stored_source(
    server_factory: Callable[..., ServerHandle],
    browser: Browser,
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    server = server_factory(
        fake_now="2026-10-27T12:00:00",
        server_module="tests.e2e.measure_sources_server",
    )
    evidence = tmp_path / "measure-source-evidence"
    evidence.mkdir()

    def request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.request(method, server.base + path, json=body, timeout=10)
        assert response.is_success, (path, response.status_code, response.text)
        result: dict[str, Any] = response.json()
        return result

    project = request(
        "POST", "/api/projects", {"name": "Measure proof", "summary": "Synthetic fixture"}
    )
    source = request(
        "POST",
        "/api/measure-sources",
        {
            "name": "Daily subscriptions",
            "project_id": project["id"],
            "account": "Fixture account",
            "responsible": {"kind": "chief", "id": "chief"},
        },
    )
    identifier = source["id"]
    origin = request("GET", "/api/test/measure-provider")["origin"]
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    # No tracing: a trace would retain the synthetic plaintext write request.
    try:
        page = context.new_page()
        page.goto(server.base + "/#/settings/measure-sources")
        card = page.locator('[data-source-name="Daily subscriptions"]')
        expect(card).to_be_visible(timeout=WAIT)
        page.wait_for_function(
            "() => window.__plannerDebug?.sseReconciliations >= 1",
            timeout=WAIT,
        )
        card.get_by_role("button", name="Manage source").click()
        controls = page.get_by_role("region", name="Controls for Daily subscriptions")
        controls.get_by_role("button", name="Add credential", exact=True).click()
        form = page.get_by_role("form", name="Add credential", exact=True)
        form.get_by_label("Credential name", exact=True).fill("provider-production")
        token = form.get_by_label("Token", exact=True)
        expect(token).to_have_attribute("type", "password")
        token.fill("discard-this-secret")
        form.get_by_role("button", name="Cancel", exact=True).click()
        controls.get_by_role("button", name="Add credential", exact=True).click()
        expect(page.get_by_label("Token", exact=True)).to_have_value("")
        form.get_by_label("Credential name", exact=True).fill("provider-production")
        form.get_by_label("Token", exact=True).fill("discard-on-navigation")
        page.get_by_role("navigation", name="Settings", exact=True).get_by_role(
            "link", name="Notifications", exact=True
        ).click()
        page.get_by_role("navigation", name="Settings", exact=True).get_by_role(
            "link", name="Measure sources", exact=True
        ).click()
        expect(card).to_be_visible(timeout=WAIT)
        card.get_by_role("button", name="Manage source").click()
        controls.get_by_role("button", name="Add credential", exact=True).click()
        expect(form.get_by_label("Token", exact=True)).to_have_value("")
        form.get_by_label("Credential name", exact=True).fill("provider-production")
        form.get_by_label("Token", exact=True).fill(FIRST_TOKEN)
        form.get_by_role("button", name="Save credential", exact=True).click()
        expect(form).to_have_count(0, timeout=WAIT)
        expect(
            controls.get_by_text("provider-production · bearer · Configured", exact=True)
        ).to_be_visible(timeout=WAIT)

        def configure(
            current: dict[str, Any], *, second: bool = False
        ) -> tuple[dict[str, Any], str]:
            source_id = current["id"]
            route = "/api/projects/fixture/query" if second else "/api/subscriptions"
            measure = request(
                "POST",
                "/api/measures",
                {
                    "project_id": project["id"],
                    "name": "Query events" if second else "Active subscriptions",
                    "unit": "events" if second else "subscriptions",
                    "origin": "taken",
                    "kind": "reading",
                    "timezone": "Europe/Berlin",
                    "source_id": source_id,
                },
            )
            extra = (
                (
                    ",method='POST',json_body={'query':{'kind':'HogQLQuery',"
                    "'query':'select count() from events'},'refresh':'blocking'}"
                )
                if second
                else ""
            )
            field = "['results'][0][0]" if second else "['value']"
            script = (
                f"response=request({origin + route!r},"
                f"credential_ref='provider-production'{extra})\n"
                "response.raise_for_status()\n"
                "output=context['required_outputs'][0]\n"
                f"record(output['measure_id'],output['required_date'],response.json(){field})"
            )
            current = request("GET", f"/api/measure-sources/{source_id}")
            current = request(
                "PUT",
                f"/api/measure-sources/{source_id}/script",
                {
                    "expected_revision": current["source_revision"],
                    "text": script,
                },
            )
            return current, str(measure["id"])

        source, measure_id = configure(source)
        expect(controls.get_by_role("button", name="Collect now", exact=True)).to_be_enabled(
            timeout=WAIT
        )
        expect(
            controls.get_by_text(
                "No saved script. Add one through the API or CLI.", exact=True
            )
        ).to_have_count(0, timeout=WAIT)
        controls.get_by_label("Daily schedule enabled", exact=True).check()
        controls.get_by_label("Daily time (server local time)").fill("09:00")
        controls.get_by_role("button", name="Save schedule", exact=True).click()
        expect(controls.get_by_text("Schedule saved.", exact=True)).to_be_visible(timeout=WAIT)
        controls.get_by_role("button", name="Collect now", exact=True).click()
        expect(card.get_by_text("42 subscriptions", exact=True)).to_be_visible(timeout=WAIT)
        expect(card.locator('[data-source-health="healthy"]')).to_be_visible(timeout=WAIT)
        page.screenshot(path=str(evidence / "success.png"), full_page=True)
        persisted = request(
            "POST",
            "/api/measures/read",
            {
                "measure_ids": [measure_id],
                "first_date": "2026-10-27",
                "last_date": "2026-10-27",
            },
        )
        assert persisted["values"][0]["value"] == 42
        assert persisted["values"][0]["date"] == "2026-10-27"

        request("POST", "/api/test/measure-provider/rotate")
        controls.get_by_role("button", name="Collect now", exact=True).click()
        expect(card.locator('[data-source-health="failed"]')).to_be_visible(timeout=WAIT)
        expect(card.locator("[data-collection-failure]")).to_contain_text(
            "rejected the credential", timeout=WAIT
        )
        expect(card.get_by_text("42 subscriptions", exact=True)).to_be_visible()
        page.screenshot(path=str(evidence / "failure.png"), full_page=True)
        controls.get_by_role("button", name="Replace provider-production", exact=True).click()
        replacement = page.get_by_role("form", name="Replace credential", exact=True)
        expect(replacement.get_by_label("Token", exact=True)).to_have_value("")
        replacement.get_by_label("Token", exact=True).fill(SECOND_TOKEN)
        replacement.get_by_role("button", name="Save credential", exact=True).click()
        expect(replacement).to_have_count(0, timeout=WAIT)

        # External request: no navigation, click, or manual refresh after this commit.
        page.evaluate("window.__measureMountedProof = 'same-document'")
        current = request("GET", f"/api/measure-sources/{identifier}")
        recovered = request(
            "POST",
            f"/api/measure-sources/{identifier}/runs",
            {
                "mode": "collection",
                "history": None,
                "expected_script_revision": current["active_script"]["revision"],
            },
        )
        expect(card.get_by_text("43 subscriptions", exact=True)).to_be_visible(timeout=WAIT)
        expect(card.locator('[data-source-health="healthy"]')).to_be_visible(timeout=WAIT)
        expect(card.locator("[data-latest-run]")).to_have_text(
            "Latest collection: succeeded", timeout=WAIT
        )
        expect(controls.locator(".run-row").first).to_contain_text(
            "Collection · succeeded", timeout=WAIT
        )
        assert page.evaluate("window.__measureMountedProof") == "same-document"
        recovery = request("GET", f"/api/measure-source-runs/{recovered['id']}")
        assert recovery["run"]["status"] == "succeeded"
        page.screenshot(path=str(evidence / "recovered.png"), full_page=True)

        second = request(
            "POST",
            "/api/measure-sources",
            {
                "name": "Provider query count",
                "project_id": project["id"],
                "account": "Fixture account",
                "responsible": {"kind": "chief", "id": "chief"},
            },
        )
        second, second_measure_id = configure(second, second=True)
        second_run = request(
            "POST",
            f"/api/measure-sources/{second['id']}/runs",
            {
                "mode": "collection",
                "history": None,
                "expected_script_revision": second["active_script"]["revision"],
            },
        )
        second_card = page.locator('[data-source-name="Provider query count"]')
        expect(second_card.get_by_text("25 events", exact=True)).to_be_visible(timeout=WAIT)
        second_detail = request("GET", f"/api/measure-source-runs/{second_run['id']}")
        assert second_detail["run"]["status"] == "succeeded"
        values = request(
            "POST",
            "/api/measures/read",
            {
                "measure_ids": [second_measure_id],
                "first_date": "2026-10-27",
                "last_date": "2026-10-27",
            },
        )
        assert values["values"][0]["value"] == 25
        assert values["values"][0]["date"] == "2026-10-27"
        state = request("GET", "/api/test/measure-provider")
        assert state["accepted"] == 3 and state["refused"] == 1 and state["post_queries"] == 1
        assert len(request("GET", "/api/measure-sources")["sources"]) == 2
        assert FIRST_TOKEN not in page.content() and SECOND_TOKEN not in page.content()
        assert (
            page.evaluate(
                "[...Object.values(localStorage),...Object.values(sessionStorage)]"
                ".some(v => ['fixture-replacement-key','fixture-first-key']"
                ".some(key => v.includes(key)))"
            )
            is False
        )
        evidence.joinpath("receipt.json").write_text(
            json.dumps(
                {
                    "wall_seconds": round(time.monotonic() - started, 3),
                    "recovery_metrics": recovery["run"]["metrics"],
                    "second_source_metrics": second_detail["run"]["metrics"],
                    "provider": state,
                    "persisted_sources": 2,
                },
                indent=2,
            )
        )
        print("MEASURE_SOURCE_BROWSER_EVIDENCE", evidence)
    finally:
        context.close()
