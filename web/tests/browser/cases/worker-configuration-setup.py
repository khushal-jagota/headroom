# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    import copy

    def model(model_id, name, efforts):
        return {
            "model_id": model_id,
            "display_name": name,
            "enabled": True,
            "reasoning_effort_options": efforts,
        }

    def backend(key, models, default_model, default_effort=None, efforts=None, diagnoses=None):
        return {
            "backend_key": key,
            "installed": True,
            "executable_path": "/probe/" + key,
            "version": "1",
            "identity": None,
            "available_models": models,
            "reasoning_effort_options": efforts or [],
            "default_model_id": default_model,
            "default_reasoning_effort": default_effort,
            "cached_usage": None,
            "update_advisory": None,
            "diagnoses": diagnoses or [],
        }

    machine = {
        "backends": [
            backend("claude", [model("claude-a", "Claude A", [])], "claude-a"),
            backend(
                "codex",
                [
                    model("codex-native", "Codex native", ["low", "high"]),
                    model("codex-deep", "Codex deep", ["low", "high"]),
                    model("codex-plain", "Codex plain", []),
                    model("codex-mini", "Codex mini", ["low", "high"]),
                    model("codex-fast", "Codex fast", ["low", "high"]),
                    model("codex-long", "Codex long", ["low", "high"]),
                    model("codex-spark", "Codex spark", ["low", "high"]),
                ],
                "codex-native",
                "low",
                ["low", "high"],
            ),
            backend("hermes", [], None, diagnoses=["Hermes has no configured model."]),
        ]
    }
    refreshed_machine = copy.deepcopy(machine)
    refreshed_machine["backends"][1]["available_models"].append(
        model("codex-refreshed", "Codex refreshed", ["low", "high"])
    )

    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto([__file__, *urls][1], wait_until="networkidle")
    page.wait_for_function("window.__requestCount() === 1")
    page.evaluate("payload => window.__respond(0, payload)", machine)

    setup = page.locator("[data-employee-configuration-setup]")
    picker = setup.locator("[data-conversation-model-picker]")
    trigger = picker.locator("[data-conversation-picker-trigger]")
    assert "codex-gone extreme" in trigger.inner_text()
    assert trigger.get_attribute("aria-controls") is None
    trigger.click()
    choices = picker.locator("[data-conversation-picker-choice]")
    panel = picker.locator("[data-conversation-picker-panel]")
    controlled = trigger.get_attribute("aria-controls")
    assert controlled and picker.locator(f"#{controlled}").get_attribute("role") == "listbox"
    assert (
        picker.locator("button").evaluate_all(
            "buttons => buttons.filter(button => button.tabIndex === 0).length"
        )
        == 1
    )
    assert choices.count() == 7
    assert picker.locator('[data-conversation-picker-choice="codex-gone"]').count() == 0
    assert picker.locator("[data-conversation-picker-chosen]").count() == 0
    assert (
        picker.locator("[data-conversation-picker-feedback]").inner_text()
        == "Codex no longer offers codex-gone."
    )
    assert panel.evaluate("el => el.scrollHeight <= el.clientHeight")
    assert picker.get_by_role("listbox").evaluate("el => el.scrollHeight <= el.clientHeight")
    assert panel.bounding_box()["height"] < 844
    picker.locator('[data-conversation-picker-choice="codex-deep"]').hover()
    assert page.evaluate("window.__ticketSaves().length") == 0
    picker.get_by_role("listbox").press("c")
    page.wait_for_function(
        "document.querySelector('[data-conversation-picker-choice=codex-plain]')?.getAttribute('data-conversation-picker-active') === 'true'"
    )
    assert (
        picker.locator('[data-conversation-picker-choice="codex-plain"]').get_attribute(
            "data-conversation-picker-active"
        )
        == "true"
    )

    refresh = picker.locator("[data-conversation-picker-refresh]")
    assert refresh.inner_text() == "Refresh"
    refresh.click()
    page.wait_for_function("window.__requestCount() === 2")
    assert page.evaluate("window.__request(1)") == {
        "url": "/api/conversation/backends/refresh",
        "method": "POST",
    }
    assert refresh.inner_text() == "Reading…"
    assert refresh.get_attribute("aria-busy") == "true"
    assert refresh.is_disabled()
    assert not trigger.is_disabled()
    assert picker.locator("[data-conversation-picker-panel]").count() == 1
    assert picker.get_by_role("listbox").evaluate("el => document.activeElement === el")
    page.evaluate(
        "payload => window.__respond(1, payload)",
        {
            **refreshed_machine,
            "usage_outcomes": [
                {"backend_key": "codex", "outcome": "succeeded", "detail": None},
                {"backend_key": "claude", "outcome": "failed", "detail": "Claude usage failed."},
                {"backend_key": "hermes", "outcome": "unavailable", "detail": "No usage source."},
            ],
        },
    )
    page.wait_for_function(
        "document.querySelector('[data-conversation-picker-refresh]').textContent.includes('Refresh')"
    )
    assert picker.locator('[data-conversation-picker-choice="codex-refreshed"]').count() == 1
    assert (
        picker.locator("[data-conversation-picker-feedback]").inner_text() == "Claude usage failed."
    )

    refresh.click()
    page.wait_for_function("window.__requestCount() === 3")
    assert not trigger.is_disabled()
    assert picker.get_by_role("listbox").evaluate("el => document.activeElement === el")
    page.evaluate("window.__reject(2)")
    page.wait_for_function(
        "document.querySelector('[data-conversation-picker-feedback]').textContent.includes('server could not be reached')"
    )
    assert picker.locator('[data-conversation-picker-choice="codex-refreshed"]').count() == 1

    page.evaluate("window.__rejectNextTicketSave()")
    picker.locator('[data-conversation-picker-choice="codex-native"]').click()
    assert picker.locator('[data-conversation-picker-choice="low"]').count() == 1
    picker.locator('[data-conversation-picker-choice="low"]').click()
    page.wait_for_function("window.__ticketSaves().length === 1")
    page.wait_for_selector("[data-employee-configuration-save-error]")
    assert "codex-gone extreme" in trigger.inner_text()
    assert picker.locator("[data-conversation-picker-panel]").count() == 0
    page.keyboard.press("Escape")
    assert trigger.get_attribute("aria-controls") is None

    trigger.click()
    picker.locator('[data-conversation-picker-choice="codex-native"]').click()
    assert picker.locator('[data-conversation-picker-choice="low"]').count() == 1
    picker.locator('[data-conversation-picker-choice="low"]').click()
    page.wait_for_function("window.__ticketSaves().length === 2")
    assert page.evaluate("window.__ticketSaves()[0]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-native",
        "employee_launch_reasoning_effort": "low",
    }

    trigger.click()
    assert picker.locator("[data-conversation-backend]").evaluate_all(
        "rows => rows.map(row => row.getAttribute('data-conversation-backend'))"
    ) == ["claude", "codex", "hermes"]
    picker.locator('[data-conversation-backend="codex"]').click()
    assert page.evaluate("window.__ticketSaves().length") == 2
    assert picker.locator("[data-conversation-picker-panel]").count() == 1
    hermes = picker.locator('[data-conversation-backend="hermes"]')
    assert hermes.get_attribute("aria-disabled") == "true"
    hermes.click(force=True)
    assert (
        picker.locator("[data-conversation-picker-feedback]").inner_text()
        == "Hermes has no configured model."
    )
    page.evaluate("window.__holdNextTicketSave()")
    picker.locator('[data-conversation-backend="claude"]').click()
    page.wait_for_function("window.__ticketSaves().length === 3")
    assert page.evaluate("window.__ticketSaves()[2]") == {
        "employee_backend": "claude",
        "employee_launch_model": "claude-a",
        "employee_launch_reasoning_effort": None,
    }
    assert picker.locator("[data-conversation-picker-panel]").count() == 1
    assert picker.locator("[data-conversation-backend-showing]").inner_text() == "Claude"
    assert picker.locator('[data-conversation-picker-choice="claude-a"]').count() == 1
    assert picker.locator("[data-conversation-picker-panel]").get_attribute("aria-busy") == "true"
    assert picker.locator('[data-conversation-picker-choice="claude-a"]').is_disabled()
    page.evaluate("window.__releaseTicketSave()")
    page.wait_for_function("!document.querySelector('[data-conversation-picker-trigger]').disabled")
    reasoning = picker.locator("[data-conversation-picker-reasoning]")
    assert reasoning.get_attribute("aria-disabled") == "true"
    assert (
        picker.locator("button").evaluate_all(
            "buttons => buttons.filter(button => button.tabIndex === 0).length"
        )
        == 1
    )
    picker.get_by_role("listbox").press("ArrowLeft")
    page.keyboard.press("End")
    assert reasoning.evaluate("el => document.activeElement === el")
    page.keyboard.press("Enter")
    assert (
        picker.locator("[data-conversation-picker-feedback]").inner_text()
        == "Claude A takes no reasoning effort."
    )
    page.keyboard.press("Escape")

    page.evaluate("window.__showDefaults()")
    page.wait_for_function("window.__requestCount() === 4")
    page.evaluate("payload => window.__respond(3, payload)", machine)
    defaults = page.locator("[data-launch-defaults]")
    default_picker = defaults.locator("[data-conversation-model-picker]")
    default_picker.locator("[data-conversation-picker-trigger]").click()
    assert default_picker.locator('[data-conversation-picker-choice="codex-gone"]').count() == 0
    assert (
        default_picker.locator("[data-conversation-picker-feedback]").inner_text()
        == "Codex no longer offers codex-gone."
    )
    default_picker.locator('[data-conversation-picker-choice="codex-plain"]').click()
    page.wait_for_function("window.__defaultSaves().length === 1")
    assert page.evaluate("window.__defaultSaves()[0]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-plain",
        "employee_launch_reasoning_effort": None,
    }
    default_picker.locator("[data-conversation-picker-trigger]").click()
    page.evaluate("window.__holdNextDefaultSave()")
    default_picker.locator('[data-conversation-backend="claude"]').click()
    page.wait_for_function("window.__defaultSaves().length === 2")
    assert default_picker.locator("[data-conversation-backend-showing]").inner_text() == "Claude"
    assert default_picker.locator('[data-conversation-picker-choice="claude-a"]').count() == 1
    assert (
        default_picker.locator("[data-conversation-picker-panel]").get_attribute("aria-busy")
        == "true"
    )
    page.evaluate("window.__releaseDefaultSave()")
    page.wait_for_function(
        "!document.querySelector('[data-launch-defaults] [data-conversation-picker-trigger]').disabled"
    )
    # The same controller rolls back a failed defaults save without affecting Ticket setup.
    page.evaluate("window.__rejectNextDefaultSave()")
    default_picker.locator('[data-conversation-backend="codex"]').click()
    page.wait_for_selector("[data-launch-defaults-error]")
    assert "Claude A" in default_picker.locator("[data-conversation-picker-trigger]").inner_text()
    assert "Claude A" in trigger.inner_text()
    assert "Default save was refused." in defaults.inner_text()

    # Dispose a pending catalogue read, then mount a fresh independent editor.
    page.evaluate("window.__hideDefaults()")
    page.wait_for_selector("[data-launch-defaults]", state="detached")
    old_request = page.evaluate("window.__requestCount()")
    page.evaluate("window.__showDefaults()")
    page.wait_for_function("count => window.__requestCount() === count + 1", arg=old_request)
    page.evaluate("window.__hideDefaults()")
    page.wait_for_selector("[data-launch-defaults]", state="detached")
    new_request = page.evaluate("window.__requestCount()")
    page.evaluate("window.__showDefaults()")
    page.wait_for_function("count => window.__requestCount() === count + 1", arg=new_request)
    page.evaluate("([index, payload]) => window.__respond(index, payload)", [new_request, machine])
    page.wait_for_function(
        "!document.querySelector('[data-launch-defaults] .worker-launch-defaults-state')"
    )
    page.evaluate("index => window.__reject(index)", old_request)
    assert "server could not be reached" not in defaults.inner_text()
    assert "Claude A" in default_picker.locator("[data-conversation-picker-trigger]").inner_text()
    pass  # Shared runner owns the browser lifecycle.

    print("unified persisted picker assertions passed")
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).with_name("configuration-gaps.py")))["run"](browser, urls[1])
