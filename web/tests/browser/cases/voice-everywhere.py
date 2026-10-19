# ruff: noqa: E501
"""Shared browser case with canonical navigation-source coverage."""

def run(browser, urls):
    import runpy
    from pathlib import Path
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'support/navigation/browser-fixture.py'))
    navigation_response = fixture['navigation_response']
    MANIFEST = {'worker_types': [{'worker_type': 'coding', 'label': 'Coding', 'stages': [{'id': 'needs_implementation', 'label': 'Implementation', 'gating_field': 'implementation', 'is_terminal': False, 'ownership_mode': 'worker'}, {'id': 'needs_consequences', 'label': 'Closeout', 'gating_field': 'consequences', 'is_terminal': False, 'ownership_mode': 'worker'}, {'id': 'done', 'label': 'Done', 'gating_field': None, 'is_terminal': True, 'ownership_mode': None}], 'advance': {'needs_implementation': 'needs_consequences', 'needs_consequences': 'done'}, 'fields': [{'id': 'implementation', 'label': 'Implementation'}, {'id': 'consequences', 'label': 'Closeout'}], 'ceiling_range': ['needs_implementation', 'needs_consequences', 'done'], 'default_ceiling': 'needs_implementation', 'worker_profile_id': 'panels-worker-coding', 'default_backend': 'codex', 'default_model': None, 'default_reasoning_effort': None}]}
    TICKET = {'id': 'review-voice', 'title': 'Review voice', 'worker_type': 'coding', 'employee_backend': 'codex', 'employee_launch_model': None, 'employee_launch_reasoning_effort': None, 'employee_configuration_editable': True, 'stage': 'needs_implementation', 'ceiling': 'done', 'ceiling_holder': {'kind': 'owner', 'id': 'owner'}, 'ticket_status': 'awaiting_approval', 'conversation_id': 'worker-session', 'conversation_history': [], 'sprint_item_title': None, 'field_values': {}, 'pending_proposal': {'field': 'implementation', 'body': 'Done', 'proposed_by': 'worker', 'created_at': 1, 'revision': 1}}

    def open_page(query):
        page = browser.new_page(viewport={'width': 1100, 'height': 760})
        page.set_default_timeout(5000)

        def snapshot(route):
            response = navigation_response({'columns': [{'cards': [TICKET]}], 'sprint_items': []}, {'chief_of_staff': {'label': 'Chief of Staff', 'conversation_id': None}})
            for record in response['snapshot']['records']:
                if record['kind'] == 'ticket':
                    proposal = TICKET['pending_proposal']
                    record['value'].update(ceiling_holder=TICKET['ceiling_holder'], gating_field='implementation', proposal_field=proposal['field'], proposal_created_at=proposal['created_at'], proposal_revision=proposal['revision'])
            route.fulfill(json=response)
        page.route('**/api/navigation/snapshot*', snapshot)
        page.route('**/api/worker-types', lambda route: route.fulfill(json=MANIFEST))
        page.route('**/api/tickets?*id=review-voice*', lambda route: route.fulfill(json=TICKET))
        page.route('**/api/conversation/voice-transcriptions', lambda route: route.fulfill(json={'transcript': 'dictated words'}))
        page.route('**/api/conversation/conversations/*/voice-transcriptions', lambda route: route.abort())
        page.goto([__file__, *urls][1] + query, wait_until='domcontentloaded')
        return page
    page = open_page('?supported=1')
    page.locator('[data-conversation-input]').wait_for(state='visible')
    assert page.locator('[data-voice-record]').is_visible()
    page.locator('[data-voice-record]').click()
    page.locator('[data-voice-stop]').click()
    page.locator('[data-conversation-input]').wait_for(state='visible')
    assert page.locator('[data-conversation-input]').input_value() == 'dictated words'
    page.locator('[data-conversation-input]').fill('dictated words, edited')
    page.locator('[data-conversation-input]').press('Enter')
    page.wait_for_function('window.__sentVoiceMessages().length === 1')
    assert page.evaluate('window.__sentVoiceMessages()') == ['dictated words, edited']
    page.close()
    page = open_page('?supported=1')
    attempts = []

    def transcribe(route):
        attempts.append(route.request.post_data_json)
        if len(attempts) == 1:
            route.fulfill(status=503, json={'detail': 'Transcription unavailable'})
        else:
            route.fulfill(json={'transcript': 'Recovered recorded draft'})
    page.route('**/api/conversation/voice-transcriptions', transcribe)
    page.locator('[data-voice-record]').click()
    page.locator('[data-voice-stop]').click()
    page.locator('[data-voice-retry]').wait_for(state='visible')
    assert page.evaluate('window.__sentVoiceMessages()') == []
    page.locator('[data-voice-retry]').click()
    page.wait_for_function("document.querySelector('[data-conversation-input]')?.value === 'Recovered recorded draft'")
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert page.evaluate('window.__sentVoiceMessages()') == []
    page.locator('[data-conversation-input]').fill('Recovered recorded draft, edited')
    page.locator('[data-conversation-input]').press('Enter')
    page.wait_for_function('window.__sentVoiceMessages().length === 1')
    assert page.evaluate('window.__sentVoiceMessages()') == ['Recovered recorded draft, edited']
    page.close()
    page = open_page('?supported=1&conversation=1')
    page.locator('[data-voice-record]').click()
    page.locator('[data-voice-stop]').click()
    page.locator('[data-conversation-input]').wait_for(state='visible')
    assert page.locator('[data-conversation-input]').input_value() == 'dictated words'
    page.close()
    page = open_page('?supported=1&coarse=1')
    assert page.locator("[data-conversation-voice='idle']").is_visible()
    assert not page.locator('[data-conversation-input]').is_visible()
    page.close()
    page = open_page('?supported=0&coarse=1')
    page.locator('[data-conversation-input]').wait_for(state='visible')
    assert page.locator('[data-voice-record]').count() == 0
    page.close()
    page = open_page('?supported=1&mode=review')
    page.locator('[data-send-back]').click()
    page.locator('[data-review-revision-input]').wait_for(state='visible')
    assert page.locator('[data-review-revision] [data-voice-record]').is_visible()
    page.locator('[data-review-revision] [data-voice-record]').click()
    page.locator('[data-review-revision] [data-voice-stop]').click()
    page.locator('[data-review-revision-input]').wait_for(state='visible')
    assert page.locator('[data-review-revision-input]').input_value() == 'dictated words'
    page.close()
    pass
