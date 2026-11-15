# ruff: noqa: E501
"""Frontend-only proof of shell geometry across independent import and resource waits."""


def run(browser, url, respond):
    import json
    import os
    import time
    from pathlib import Path
    from urllib.parse import parse_qs

    from playwright.sync_api import expect

    started = time.monotonic()
    artifacts = Path(
        os.environ.get("PANELS_APPROVAL_ARTIFACT_DIR", "data/verification/workspace-skeletons")
    )
    artifacts.mkdir(parents=True, exist_ok=True)
    evidence = []

    def run_case(width, slow):
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors, pending, chunks = [], [], []
        hold = {"navigation": True, "ticket": True, "chunk": True}
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(
            """window.__shifts=[];new PerformanceObserver(list=>{for(const entry of list.getEntries())if(!entry.hadRecentInput)window.__shifts.push({value:entry.value,sources:entry.sources.map(source=>({tag:source.node?.className,previous:source.previousRect.toJSON(),current:source.currentRect.toJSON()}))});}).observe({type:'layout-shift',buffered:true});"""
        )

        def resource(route):
            path = route.request.url.split("/api/", 1)[1]
            if path.startswith("resource-body?"):
                path = parse_qs(path.split("?", 1)[1])["key"][0].removeprefix("/api/")
            kind = (
                "navigation"
                if path.startswith("navigation/")
                else "ticket"
                if path.startswith("tickets/") and path.endswith("/workspace")
                else None
            )
            if kind and hold[kind]:
                pending.append((kind, route))
            else:
                respond(route)

        def chunk(route):
            if hold["chunk"] and "TicketRoute-" in route.request.url:
                chunks.append(route)
            else:
                route.continue_()

        def release(kind):
            hold[kind] = False
            for entry in list(pending):
                if entry[0] == kind:
                    pending.remove(entry)
                    respond(entry[1])

        def frame():
            page.evaluate(
                "() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
            )

        def geometry():
            return page.evaluate(
                """() => Object.fromEntries(['.board-workspace-shell','.board-workspace-right','.rail-utilities'].map(selector=>{const node=document.querySelector(selector);const rect=node.getBoundingClientRect();return [selector,{x:rect.x,y:rect.y,width:rect.width,height:rect.height}]}))"""
            )

        page.route("**/*.js", chunk)
        page.route("**/api/**", resource)
        page.goto(url, wait_until="domcontentloaded")
        expect(page.locator('.rail-work [data-shape="block"][aria-busy="true"]')).to_have_count(1)
        expect(
            page.locator('.board-workspace-right [data-shape="document"][aria-busy="true"]')
        ).to_have_count(1)
        before = geometry()
        if slow:
            # Wait for the real entrance, so the screenshot records a visible skeleton.
            page.wait_for_function(
                "[...document.querySelectorAll('.skeleton')].some(node=>parseFloat(getComputedStyle(node).opacity)>.9)"
            )
            page.screenshot(path=str(artifacts / f"workspace-{width}-import.png"))
        hold["chunk"] = False
        for route in chunks:
            route.continue_()
        expect(page.locator('[data-screen="ticket"]')).to_have_count(1)
        expect(
            page.locator('[data-screen="ticket"] [data-shape="document"][aria-busy="true"]')
        ).to_have_count(1)
        release("navigation")
        expect(page.locator('.rail-work [data-shape="block"][aria-busy="true"]')).to_have_count(0)
        if slow:
            page.screenshot(path=str(artifacts / f"workspace-{width}-resource.png"))
        release("ticket")
        expect(page.locator('[data-ticket-masthead-id="t_coding"]')).to_be_visible()
        expect(page.locator("[data-conversation-input]")).to_be_visible()
        expect(page.locator(".board-workspace-right .skeleton")).to_have_count(0)
        frame()
        after = geometry()
        assert before == after, {"width": width, "before": before, "after": after}
        # A same-module address update must retain the pane and its unsent draft.
        composer = page.locator("[data-conversation-input]")
        composer.fill("Unsent draft survives address changes")
        composer.evaluate("node => window.__originalComposer = node")
        page.evaluate("window.__workspaceAddressChange()")
        frame()
        assert composer.evaluate("node => node === window.__originalComposer")
        expect(composer).to_have_value("Unsent draft survives address changes")
        assert errors == [], errors
        page.screenshot(path=str(artifacts / f"workspace-{width}-loaded.png"))
        shifts = page.evaluate("window.__shifts")
        evidence.append(
            {
                "width": width,
                "wait": "slow" if slow else "fast",
                "heldImportChunks": len(chunks),
                "before": before,
                "after": after,
                "layoutShift": sum(row["value"] for row in shifts),
                "shifts": shifts,
                "browserErrors": errors,
            }
        )
        page.close()

    for width, slow in [(1440, True), (390, False)]:
        run_case(width, slow)
    (artifacts / "workspace-skeletons.json").write_text(
        json.dumps({"seconds": time.monotonic() - started, "cases": evidence}, indent=2)
    )
    print(f"Workspace skeleton route proof: {time.monotonic() - started:.2f}s", flush=True)
