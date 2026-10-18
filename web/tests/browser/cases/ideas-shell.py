# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    import os
    import pathlib

    def screenshot(page, name):
        directory = os.environ.get("PANELS_UI_PROOF_DIR")
        if directory:
            pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(pathlib.Path(directory) / (name + ".png")), full_page=True)

    page = browser.new_page()
    page.goto([__file__, *urls][1] + "#/backlog?project=project_vylo", wait_until="networkidle")
    page.locator('section[data-screen="workspace"]').wait_for()
    assert page.evaluate("window.location.hash") == "#/workspace"
    assert (
        page.locator(
            '[data-create="ticket"], [data-backlog-tickets], [data-outcome-picker]'
        ).count()
        == 0
    )

    # The footer and compact fallback expose each utility exactly once.
    for width, selected in [(1280, False), (390, False), (390, True)]:
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate(
            "window.location.hash = "
            + ("'#/workspace/item/item_offboard'" if selected else "'#/workspace'")
        )
        page.locator('section[data-screen="workspace"]').wait_for()
        more = page.get_by_role("button", name="More", exact=False)
        menu_visible = more.is_visible()
        if menu_visible:
            more.click()
        for label in ["Settings", "Ideas", "Feedback", "Usage and Backends"]:
            assert page.get_by_role("link", name=label, exact=True).count() == 1, (
                width,
                selected,
                label,
            )
        screenshot(page, "shell-utilities-" + str(width) + ("-selected" if selected else "-rail"))
        if menu_visible:
            page.keyboard.press("Escape")
            assert more.evaluate("n => n === document.activeElement")

    # Config detail keys must remount its initial-prop snapshot on in-shell changes.
    for old, canonical, kind, ident in [
        ("#/config/worker-skill", "#/settings/config/worker-skill", "skill", "panels-worker"),
        ("#/agents/workers/coding", "#/settings/config/workers/coding", "worker", "coding"),
        (
            "#/config/sprint-item-supervisor",
            "#/settings/config/sprint-item-supervisor",
            "skill",
            "panels-sprint-item-supervisor",
        ),
        (
            "#/workers/type%2Fwith%20space",
            "#/settings/config/workers/type%2Fwith%20space",
            "worker",
            "type/with space",
        ),
    ]:
        page.evaluate("hash => window.location.hash = hash", old)
        page.wait_for_function("hash => window.location.hash === hash", arg=canonical)
        page.locator(
            '[data-screen="config"][data-role-kind="' + kind + '"][data-role-id="' + ident + '"]'
        ).wait_for()
    for old, canonical, screen in [
        ("#/scheduled-tasks", "#/settings/scheduled-tasks", "scheduled-tasks"),
        ("#/notifications", "#/settings/notifications", "notifications"),
    ]:
        page.evaluate("hash => window.location.hash = hash", old)
        page.wait_for_function("hash => window.location.hash === hash", arg=canonical)
        page.locator('[data-screen="' + screen + '"]').wait_for()
        assert page.get_by_role("navigation", name="Settings", exact=True).count() == 1
        for width in [390, 1280]:
            page.set_viewport_size({"width": width, "height": 900})
            screenshot(page, "settings-" + screen + "-" + str(width))
    page.evaluate("window.location.hash = '#/settings/config'")
    page.locator('[data-screen="config"][data-role-kind="index"]').wait_for()
    for width in [390, 1280]:
        page.set_viewport_size({"width": width, "height": 900})
        screenshot(page, "settings-config-" + str(width))
    page.evaluate("window.location.hash = '#/settings/config/unknown/detail'")
    page.get_by_text("no such screen", exact=True).wait_for()
    page.set_viewport_size({"width": 1280, "height": 900})

    # A canonical direct Item address survives the empty board response and mounts
    # the real Item workspace, even though the board rail has no row for it.
    page.evaluate("window.location.hash = '#/workspace/item/item_offboard'")
    page.locator('[data-sprint-item-workspace="item_offboard"] [data-sprint-item-brief]').wait_for()
    assert (
        page.locator("[data-sprint-item-brief]").inner_text()
        == "The off-board brief remains readable."
    )
    page.wait_for_function(
        """() => window.__requests().some(request => request.path === "/api/board")"""
    )
    page.wait_for_timeout(50)
    assert page.evaluate("window.location.hash") == "#/workspace/item/item_offboard"

    page.evaluate("window.location.hash = '#/ideas'")
    page.locator('section[data-screen="ideas"]').wait_for()

    # Title-only ideas are flat.
    page.locator('[data-create="idea"] [data-input="title"]').fill(
        "Dark mode only, skip the light theme"
    )
    page.locator('[data-create="idea"] [data-commit]').click()
    flat = page.locator("[data-ideas] div.list-row[data-idea-id]")
    flat.wait_for()
    assert "Dark mode only, skip the light theme" in flat.inner_text()
    assert page.locator("[data-ideas] details.disclosure--idea").count() == 0

    # A body changes the real rendered component to a closed disclosure. Capturing the
    # first idea clears the title, and that clearing lands when the POST resolves — so
    # the next title is typed only once the box is empty. Typed before, the clearing
    # wipes it and the Capture button stays disabled.
    page.wait_for_function(
        "selector => document.querySelector(selector).value === ''",
        arg='[data-create="idea"] [data-input="title"]',
    )
    page.locator('[data-create="idea"] [data-input="title"]').fill("One-question onboarding")
    page.locator('[data-create="idea"] [data-input="body"]').fill(
        "Ask one thing that matters, infer the rest."
    )
    page.locator('[data-create="idea"] [data-commit]').click()
    details = page.locator("[data-ideas] details.disclosure--idea[data-idea-id]")
    details.wait_for()
    assert "One-question onboarding" in details.locator(".it").inner_text()
    assert "Ask one thing that matters" in details.locator(".disclosure-body").text_content()
    assert details.get_attribute("open") is None
    details.locator("> summary").click()
    page.locator("[data-ideas] details.disclosure--idea[open]").wait_for()
    assert page.locator("[data-ideas] div.list-row[data-idea-id]").count() == 1
    pass  # Shared runner owns the browser lifecycle.

    print("ideas and shell component assertions passed")
