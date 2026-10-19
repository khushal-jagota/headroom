"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    MANIFEST = {
        "worker_types": [
            {
                "worker_type": "coding",
                "label": "Coding",
                "stages": [
                    {
                        "id": "needs_implementation",
                        "label": "Implementation",
                        "gating_field": "implementation",
                        "is_terminal": False,
                        "ownership_mode": "worker",
                    },
                    {
                        "id": "needs_consequences",
                        "label": "Closeout",
                        "gating_field": "consequences",
                        "is_terminal": False,
                        "ownership_mode": "worker",
                    },
                    {
                        "id": "done",
                        "label": "Done",
                        "gating_field": None,
                        "is_terminal": True,
                        "ownership_mode": None,
                    },
                ],
                "advance": {
                    "needs_implementation": "needs_consequences",
                    "needs_consequences": "done",
                },
                "fields": [
                    {"id": "implementation", "label": "Implementation"},
                    {"id": "consequences", "label": "Closeout"},
                ],
                "ceiling_range": ["needs_implementation", "needs_consequences", "done"],
                "default_ceiling": "needs_implementation",
                "worker_profile_id": "panels-worker-coding",
                "default_backend": "codex",
                "default_model": None,
                "default_reasoning_effort": None,
            }
        ]
    }
    TICKET = {
        "id": "review-voice",
        "title": "Review voice",
        "worker_type": "coding",
        "employee_backend": "codex",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": None,
        "employee_configuration_editable": True,
        "stage": "needs_implementation",
        "ceiling": "done",
        "ceiling_holder": {"kind": "owner", "id": "owner"},
        "sprint_item_title": None,
        "ticket_status": "awaiting_approval",
        "conversation_id": "worker-session",
        "conversation_history": [],
        "field_values": {},
        "pending_proposal": {
            "field": "implementation",
            "body": "Done",
            "proposed_by": "worker",
            "created_at": 1,
        },
    }

    def open_page(query):
        page = browser.new_page(viewport={"width": 1100, "height": 760})
        page.set_default_timeout(5_000)
        page.route("**/api/worker-types", lambda route: route.fulfill(json=MANIFEST))
        page.route("**/api/tickets?*id=review-voice*", lambda route: route.fulfill(json=TICKET))
        page.route(
            "**/api/conversation/voice-transcriptions",
            lambda route: route.fulfill(json={"transcript": "dictated words"}),
        )
        page.route(
            "**/api/conversation/conversations/*/voice-transcriptions", lambda route: route.abort()
        )
        page.goto([__file__, *urls][1] + query, wait_until="domcontentloaded")
        return page

    # Fine-pointer desktop gets the microphone even before a conversation exists.
    page = open_page("?supported=1")
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-voice-record]").is_visible()
    page.locator("[data-voice-record]").click()
    page.locator("[data-voice-stop]").click()
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-conversation-input]").input_value() == "dictated words"
    page.locator("[data-conversation-input]").fill("dictated words, edited")
    page.locator("[data-conversation-input]").press("Enter")
    page.wait_for_function("window.__sentVoiceMessages().length === 1")
    assert page.evaluate("window.__sentVoiceMessages()") == ["dictated words, edited"]
    page.close()

    # Refused transcription keeps the recording. Retry restores an editable draft,
    # and never sends the transcript without the user's deliberate send.
    page = open_page("?supported=1")
    attempts = []

    def transcribe(route):
        attempts.append(route.request.post_data_json)
        if len(attempts) == 1:
            route.fulfill(status=503, json={"detail": "Transcription unavailable"})
        else:
            route.fulfill(json={"transcript": "Recovered recorded draft"})

    page.route("**/api/conversation/voice-transcriptions", transcribe)
    page.locator("[data-voice-record]").click()
    page.locator("[data-voice-stop]").click()
    page.locator("[data-voice-retry]").wait_for(state="visible")
    assert page.evaluate("window.__sentVoiceMessages()") == []
    page.locator("[data-voice-retry]").click()
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]')?.value === 'Recovered recorded draft'"
    )
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert page.evaluate("window.__sentVoiceMessages()") == []
    page.locator("[data-conversation-input]").fill("Recovered recorded draft, edited")
    page.locator("[data-conversation-input]").press("Enter")
    page.wait_for_function("window.__sentVoiceMessages().length === 1")
    assert page.evaluate("window.__sentVoiceMessages()") == ["Recovered recorded draft, edited"]
    page.close()

    # An existing conversation uses the same conversation-independent route.
    page = open_page("?supported=1&conversation=1")
    page.locator("[data-voice-record]").click()
    page.locator("[data-voice-stop]").click()
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-conversation-input]").input_value() == "dictated words"
    page.close()

    # Coarse pointer changes only the fresh empty presentation to voice-first.
    page = open_page("?supported=1&coarse=1")
    assert page.locator("[data-conversation-voice='idle']").is_visible()
    assert not page.locator("[data-conversation-input]").is_visible()
    page.close()

    # Unsupported browsers keep the ordinary composer and expose no dead control.
    page = open_page("?supported=0&coarse=1")
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-voice-record]").count() == 0
    page.close()

    # Proposal revision voice is available inside the shared Send it back action.
    page = open_page("?supported=1&mode=review")
    page.locator("[data-send-back]").click()
    page.locator("[data-review-revision-input]").wait_for(state="visible")
    assert page.locator("[data-review-revision] [data-voice-record]").is_visible()
    page.locator("[data-review-revision] [data-voice-record]").click()
    page.locator("[data-review-revision] [data-voice-stop]").click()
    page.locator("[data-review-revision-input]").wait_for(state="visible")
    assert page.locator("[data-review-revision-input]").input_value() == "dictated words"
    page.close()

    pass  # Shared runner owns the browser lifecycle.
