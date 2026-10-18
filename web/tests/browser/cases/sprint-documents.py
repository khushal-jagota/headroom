# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    from playwright.sync_api import expect

    try:
        page = browser.new_page()
        page.goto([__file__, *urls][1], wait_until="networkidle")
        # The explicit historical record renders despite unavailable Today, current
        # Sprint and project catalogue. Its stored labels are enough to read tracking.
        expect(page.get_by_role("heading", name="A useful historical sprint")).to_be_visible()
        expect(page.locator("[data-sprint-bet]")).to_have_text("Original summary")
        assert (
            page.locator("a.sprint-docs-link").get_attribute("href")
            == "#/sprint/documents?sprint=sp_test"
        )
        assert page.locator(".sprint-meta-line .sep").count() == 0
        outcome = page.locator('[data-outcome-id="outcome_existing"]')
        expect(outcome.locator(".sprint-item-rollup")).to_have_text("1/2")
        assert outcome.locator("a").get_attribute("href") == "#/workspace/item/outcome_existing"
        expect(
            page.locator('[data-sprint-project="project_one"] .board-workspace-bucket-label')
        ).to_have_text("One")
        no_outcome = page.locator("[data-sprint-no-outcome]")
        expect(no_outcome.locator(":scope > summary .sprint-item-rollup")).to_have_text("1/2")
        assert no_outcome.get_attribute("open") is None
        no_outcome.locator(":scope > summary").click()
        assert no_outcome.locator("[data-sprint-ticket-id]:visible").count() == 2
        assert (
            no_outcome.locator('[data-sprint-ticket-id="t_loose_one"]').get_attribute("href")
            == "#/workspace/t_loose_one"
        )
        assert (
            no_outcome.locator('[data-sprint-ticket-id="t_loose_two"]').get_attribute("href")
            == "#/workspace/t_loose_two"
        )
        assert page.get_by_role("button", name="Add outcome").count() == 0
        assert page.locator("[contenteditable], [data-outcome-picker]").count() == 0
        for width in (1280, 390):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(
                "() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= innerWidth + 1"
            )

        page.get_by_role("button", name="Switch view").click()
        expect(page.get_by_role("heading", name="Sprint documents")).to_be_visible()
        assert page.locator("a.sprint-back").get_attribute("href") == "#/sprint?sprint=sp_test"
        assert page.locator("[data-field]").count() == 4
        for field, text in [
            ("primary_bet", "Original summary"),
            ("kickoff", "Original plan."),
            ("checkpoint", "Original checkpoint"),
            ("review", "Original review"),
        ]:
            disclosure = page.locator(f'[data-phase="{field}"]')
            if disclosure.count() and disclosure.get_attribute("open") is None:
                disclosure.locator(":scope > summary").click()
            expect(page.locator(f'[data-field="{field}"]')).to_contain_text(text)
        proof = page.get_by_role("link", name="Open plan.md", exact=True)
        expect(proof).to_be_visible()
        assert proof.get_attribute("href").startswith("#/preview?")
        assert "plan.md" in proof.get_attribute("href")
        expect(page.locator('[data-field="kickoff"]')).to_contain_text("Historical artifact proof")
        assert page.locator("[contenteditable], input, textarea").count() == 0
        assert page.get_by_role("textbox").count() == 0
        # Legacy historical Item address offers exact current navigation, without
        # embedding an editable workspace or asking for a current Item payload.
        page.evaluate("window.showLegacyItem()")
        expect(page.locator("[data-historical-orchestrator]")).to_have_attribute(
            "href", "#/workspace/item/outcome_existing"
        )
        assert page.locator("[contenteditable], input, textarea").count() == 0
        saved = page.evaluate("window.saved()")
        assert saved["sprint"]["primary_bet"] == "Original **summary**"
        assert all(request["method"] == "GET" for request in saved["requests"]), saved["requests"]
        paths = [request["path"] for request in saved["requests"]]
        assert "/api/sprints/sp_test/tracking" in paths
        assert "/api/day/today" not in paths and "/api/sprint/current" not in paths, paths
        assert not any("/collections/" in path for path in paths)
    finally:
        pass  # Shared runner owns the browser lifecycle.
    print(
        "historical sprint tracking and read-only documents survive missing live calendar/catalogue"
    )
