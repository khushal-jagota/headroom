"""Bounded owner-review probe, outside the test suites.

The existing conversation browser case owns component behavior. This probe checks
one delivery gap: the compiled review artifact inside the deployed managed HTML
Blob sandbox. It uses no extra server and sends no live conversation messages.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page, sync_playwright

NATIVE_CHARACTER = """held => {
 const thread=document.querySelector('[data-conversation-thread]');
 if(!thread||!held)return null;
 const viewport=thread.getBoundingClientRect();
 const walker=document.createTreeWalker(thread,NodeFilter.SHOW_TEXT);
 for(let node;node=walker.nextNode();){
   if(!node.parentElement.closest('.markdown-block')||node.textContent!==held.text)continue;
   const range=document.createRange();
   range.setStart(node,held.character);range.setEnd(node,held.character+1);
   const rect=range.getBoundingClientRect();
   return {text:node.textContent,character:held.character,offset:rect.top-viewport.top,
      visible:rect.bottom>viewport.top&&rect.top<viewport.bottom};
 }return null;
}"""

MEASURE = """() => {
 const box=document.querySelector('[data-conversation-box]');
 const input=document.querySelector('[data-conversation-input]');
 const layer=document.querySelector('.conversation-layer');
 const pane=document.querySelector('[data-conversation-state]');
 const rect=e=>e?Object.fromEntries(
  ['x','y','width','height'].map(k=>[k,e.getBoundingClientRect()[k]])):null;
 const properties=['paddingTop','paddingRight','paddingBottom','paddingLeft','borderRadius',
  'borderLeftWidth','borderColor','backgroundColor','backgroundImage','outline','lineHeight','fontSize'];
 const style=e=>e?Object.fromEntries(properties.map(k=>[k,getComputedStyle(e)[k]])):null;
 return {viewport:{width:innerWidth,height:innerHeight},box:rect(box),input:rect(input),
  layer:rect(layer),boxStyle:style(box),inputStyle:style(input),layerStyle:style(layer),
  state:pane?.dataset.conversationState,
  textInset:input&&box?input.getBoundingClientRect().left-box.getBoundingClientRect().left
    +parseFloat(getComputedStyle(input).paddingLeft):null,
  focus:document.activeElement===input,scrollWidth:document.documentElement.scrollWidth};
}"""


def child_frame(parent: Page | Frame, selector: str) -> Frame:
    element = parent.locator(selector).first.element_handle()
    assert element is not None, selector
    frame = element.content_frame()
    assert frame is not None, selector
    return frame


def settle(page: Page) -> None:
    page.wait_for_timeout(120)


def managed(page: Page, ticket: str, path: str, base_url: str = "http://127.0.0.1:8767") -> Frame:
    page.goto(f"{base_url}/#/preview?source=ticket&ticket={ticket}&path=artifacts%2F{path}")
    page.locator('iframe[sandbox="allow-scripts"]').first.wait_for()
    frame = child_frame(page, 'iframe[sandbox="allow-scripts"]')
    frame.wait_for_load_state()
    return frame


def original(page: Page, host: str, width: int, base_url: str) -> tuple[Frame, Frame]:
    wrapper = managed(page, "t_4qpsqww3", "design.html", base_url)
    wrapper.locator("#host-choice").select_option(host)
    wrapper.locator("[data-refinement=messages]").select_option("none")
    wrapper.locator(f"[data-context={'mobile' if width == 390 else 'desktop'}]").click()
    # Match the content iframe, not the mockup's placeholder rail or mobile chrome.
    wrapper.evaluate(
        """width=>{
      const app=document.querySelector('#live-app');
      app.style.cssText=`width:${width}px;min-width:0;max-width:none;height:800px;margin:0;border:0;padding:0`;
      document.querySelectorAll('.sidebar,.mobile-nav').forEach(e=>e.style.display='none');
      document.querySelector('.page').style.cssText='padding:0;margin:0;max-width:none';
      document.querySelector('.inspection').style.cssText=`border:0;width:${width}px`;
    }""",
        width,
    )
    frame = child_frame(wrapper, "#design-frame")
    frame.locator("[data-conversation-input]").wait_for()
    settle(page)
    return wrapper, frame


def capture(frame: Frame, page: Page) -> dict[str, Any]:
    page.mouse.move(0, 0)
    frame.locator("[data-conversation-input]").evaluate("(e)=>e.blur()")
    settle(page)
    rest = frame.evaluate(MEASURE)
    frame.locator("[data-conversation-rest-bar]").hover()
    settle(page)
    hover = frame.evaluate(MEASURE)
    frame.locator("[data-conversation-input]").focus()
    settle(page)
    focus = frame.evaluate(MEASURE)
    return {"rest": rest, "hover": hover, "focus": focus}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8767")
    parser.add_argument("--ticket", default="t_2g0vwu4y")
    parser.add_argument("--path", default="candidate/preview.html")
    parser.add_argument(
        "--widths", nargs="+", type=int, choices=(736, 1250, 390), default=[736, 1250, 390]
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        choices=("ticket", "manager", "chief", "artifact"),
        default=["ticket", "manager", "chief", "artifact"],
    )
    parser.add_argument("--skip-layout", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result: dict[str, Any] = {
        "purpose": (
            "Manual review acceptance only. Existing browser coverage owns component behavior. "
            "This probe checks managed Blob delivery and matched visual evidence. "
            "No new test host or server."
        ),
        "hostDimensionNote": (
            "Widths name the content host. The original placeholder sidebar "
            "and mobile navigation are excluded. Both hosts use 800px height. "
            "The cropped owner screenshot establishes no outer viewport width."
        ),
        "selectedWidths": args.widths,
        "selectedHosts": args.hosts,
        "layoutSkipped": args.skip_layout,
        "cases": [],
        "pageErrors": [],
        "previewRequests": [],
    }
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1800, "height": 1400}, reduced_motion="reduce"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: result["pageErrors"].append(str(error)))
        page.on(
            "console",
            lambda message: (
                print("PREVIEW", message.text, flush=True) if message.type == "error" else None
            ),
        )
        page.on(
            "request",
            lambda request: (
                result["previewRequests"].append(
                    {"url": request.url, "method": request.method, "frame": request.frame.url}
                )
                if request.frame.url.startswith(("blob:", "about:srcdoc"))
                else None
            ),
        )
        wrapper = managed(page, args.ticket, args.path, args.base_url)
        wrapper.locator("iframe").wait_for()
        review = child_frame(wrapper, "iframe")
        review.locator("[data-conversation-input]").wait_for()
        result["managedFrame"] = {
            "url": review.url,
            "sandbox": page.locator("iframe").first.get_attribute("sandbox"),
            "origin": review.evaluate("location.origin"),
        }
        assert wrapper.url.startswith("blob:"), wrapper.url
        assert result["managedFrame"]["origin"] == "null", result["managedFrame"]
        assert result["managedFrame"]["sandbox"] == "allow-scripts", result["managedFrame"]
        build_text = review.locator("#preview-build").text_content()
        assert build_text is not None
        result["build"] = json.loads(build_text)
        # Host/width controls are the review artifact's public controls.
        for width in () if args.skip_layout else args.widths:
            for host in args.hosts:
                review.get_by_label("Host", exact=True).select_option(host)
                review.get_by_label("Host width", exact=True).select_option(str(width))
                settle(page)
                review.locator(".review-stage").evaluate(
                    '(e)=>{e.style.flex="none";e.style.height="800px"}'
                )
                real = capture(review, page)
                real["host"] = host
                real["width"] = width
                original_page = context.new_page()
                original_page.on(
                    "pageerror",
                    lambda error: result["pageErrors"].append("original: " + str(error)),
                )
                _, mock = original(original_page, host, width, args.base_url)
                reference = capture(mock, original_page)
                for name, data in [("real", real), ("approved", reference)]:
                    assert data["rest"]["viewport"]["width"] == width, (host, width, name, data)
                    host_box = review.locator("[data-review-host]").bounding_box()
                    assert host_box is not None and host_box["width"] == width
                    assert data["rest"]["boxStyle"]["borderRadius"] == "12px", (
                        host,
                        width,
                        name,
                        data,
                    )
                    assert data["rest"]["textInset"] >= 16, (host, width, name, data)
                draft = "\n".join(f"Review draft line {i}" for i in range(20))
                textarea = review.locator("[data-conversation-input]")
                baseline = real["rest"]["input"]["height"]
                textarea.fill(draft)
                settle(page)
                real["grown"] = review.evaluate(MEASURE)
                assert real["grown"]["input"]["height"] > baseline
                if review.evaluate(MEASURE)["state"] != "opened":
                    review.get_by_role("button", name="Open conversation", exact=True).click()
                settle(page)
                real["opened"] = review.evaluate(MEASURE)
                assert real["opened"]["state"] == "opened"
                assert textarea.input_value() == draft
                mock.locator("[data-conversation-input]").fill(draft)
                if mock.evaluate(MEASURE)["state"] != "opened":
                    mock.locator("[data-conversation-rest-bar]").click()
                settle(original_page)
                reference["opened"] = mock.evaluate(MEASURE)
                if True:
                    # Save screenshots of each actual host, without approximation markup.
                    real["screenshots"] = {}
                    reference["screenshots"] = {}
                    for state in ("opened",):
                        a = f"real-{host}-{width}-{state}.png"
                        b = f"approved-{host}-{width}-{state}.png"
                        review.locator("[data-review-host]").screenshot(path=str(args.output / a))
                        mock.frame_element().screenshot(path=str(args.output / b))
                        real["screenshots"][state] = a
                        reference["screenshots"][state] = b
                review.get_by_role("button", name="Return to dock", exact=True).click()
                settle(page)
                assert textarea.input_value() == draft
                real["draftRetainedAfterDock"] = True
                textarea.fill("")
                if True:
                    a = f"real-{host}-{width}-rest.png"
                    b = f"approved-{host}-{width}-rest.png"
                    review.locator("[data-review-host]").screenshot(path=str(args.output / a))
                    mock.locator("[data-conversation-collapse]").click()
                    settle(original_page)
                    mock.locator("[data-conversation-input]").fill("")
                    settle(original_page)
                    mock.frame_element().screenshot(path=str(args.output / b))
                    real["screenshots"]["rest"] = a
                    reference["screenshots"]["rest"] = b
                result["cases"].append({"real": real, "approved": reference})
                (args.output / "preview-browser-evidence.json").write_text(
                    json.dumps(result, indent=2) + "\n"
                )
                print(f"PASS matched {host} {width}", flush=True)
                original_page.close()
        result["savedPositionCases"] = []
        for width in args.widths:
            for host in args.hosts:
                review.get_by_label("Host", exact=True).select_option(host)
                review.get_by_label("Host width", exact=True).select_option(str(width))
                for scenario in ("restored", "timeout", "unavailable", "fetch-error"):
                    start = time.monotonic()
                    review.get_by_label("Saved position", exact=True).select_option(scenario)
                    review.locator('[data-preview-phase="restoration"]').wait_for()
                    anchor = review.locator("[data-preview-anchor]").get_attribute(
                        "data-preview-anchor"
                    )
                    assert anchor and json.loads(anchor).get("content"), (
                        host,
                        width,
                        scenario,
                        anchor,
                    )
                    held_text = review.locator("[data-preview-character]").get_attribute(
                        "data-preview-character"
                    )
                    assert held_text is not None
                    held_character = json.loads(held_text)
                    assert held_character, (host, width, scenario)
                    native_after = None
                    if scenario == "restored":
                        review.wait_for_function("window.__returnProbe.targetBodyWaiting>0")
                        review.get_by_role("button", name="Release saved body", exact=True).click()
                        review.wait_for_function("""() => {
                          const target=document.querySelector(
                            '[data-conversation-view-key="e2000"] .markdown-host');
                          const thread=document.querySelector('[data-conversation-thread]');
                          return target?.textContent.includes('Late paragraph 2000.') &&
                            thread.scrollHeight-thread.clientHeight-thread.scrollTop>20 &&
                            !document.querySelector('[data-conversation-restoration-status]');
                        }""")
                        review.wait_for_function(
                            """held => {
                          const region=document.querySelector('.conversation-transcript-region');
                          return region?.getAttribute('aria-busy')==='false';
                        }""",
                            arg=held_character,
                        )
                        native_after = review.evaluate(NATIVE_CHARACTER, held_character)
                        assert native_after and native_after["visible"], (host, width, native_after)
                        assert abs(native_after["offset"] - held_character["offset"]) <= 1, (
                            host,
                            width,
                            held_character,
                            native_after,
                        )
                    elif scenario == "fetch-error":
                        review.get_by_text(
                            "Local transport · restoration · reader idle · message fetch error",
                            exact=True,
                        ).wait_for()
                        assert review.locator("[data-conversation-view-key]").count() > 0
                    else:
                        review.wait_for_function(
                            """() => {
                          const thread=document.querySelector('[data-conversation-thread]');
                          return document.querySelector('[data-conversation-view-key="e2100"]') &&
                            Math.abs(thread.scrollHeight-thread.clientHeight-thread.scrollTop)<=2 &&
                            !document.querySelector('[data-conversation-restoration-status]');
                        }""",
                            timeout=15000,
                        )
                    assert review.locator("[data-conversation-restoration-status]").count() == 0
                    assert (
                        review.get_by_role(
                            "button", name="Retry conversation position", exact=True
                        ).count()
                        == 0
                    )
                    assert review.evaluate("innerWidth") == width
                    scroll = review.locator("[data-conversation-thread]").evaluate(
                        "(e)=>({top:e.scrollTop,max:e.scrollHeight-e.clientHeight})"
                    )
                    if scenario == "timeout":
                        review.get_by_role("button", name="Release saved body", exact=True).click()
                        settle(page)
                        after = review.locator("[data-conversation-thread]").evaluate(
                            "(e)=>({top:e.scrollTop,max:e.scrollHeight-e.clientHeight})"
                        )
                        assert abs(after["top"] - after["max"]) <= 2, after
                    result["savedPositionCases"].append(
                        {
                            "host": host,
                            "width": width,
                            "scenario": scenario,
                            "capturedAnchor": json.loads(anchor),
                            "heldCharacter": held_character,
                            "restoredCharacter": native_after,
                            "scroll": scroll,
                            "elapsedSeconds": round(time.monotonic() - start, 3),
                        }
                    )
                    (args.output / "preview-browser-evidence.json").write_text(
                        json.dumps(result, indent=2) + "\n"
                    )
                    print(f"PASS saved position {host} {width} {scenario}", flush=True)
        review.get_by_label("Saved position", exact=True).select_option("normal")
        review.locator('[data-preview-phase="ready"]').wait_for()
        review.locator("[data-conversation-input]").fill("Local acceptance probe")
        review.locator("[data-conversation-send]").click()
        review.get_by_text(
            "This is a local sample reply. Your message stays in this preview.", exact=True
        ).first.wait_for()
        result["localSend"] = {
            "replyVisible": True,
            "noLiveSend": not any(r["method"] != "GET" for r in result["previewRequests"]),
        }
        assert result["localSend"]["noLiveSend"]
        # Opaque-origin storage access is blocked. Exercise the real mobile
        # keyboard-preference path, which catches that SecurityError.
        mobile = browser.new_context(
            viewport={"width": 390, "height": 1100},
            has_touch=True,
            is_mobile=True,
            reduced_motion="reduce",
        )
        mobile_page = mobile.new_page()
        mobile_page.on(
            "pageerror", lambda error: result["pageErrors"].append("mobile: " + str(error))
        )
        mobile_wrapper = managed(mobile_page, args.ticket, args.path, args.base_url)
        mobile_wrapper.locator("iframe").wait_for()
        mobile_review = child_frame(mobile_wrapper, "iframe")
        mobile_review.get_by_label("Host width", exact=True).select_option("390")
        mobile_review.get_by_role("button", name="Type instead", exact=True).click()
        mobile_review.locator("[data-conversation-input]").fill("Mobile draft stays local")
        result["mobile"] = {
            "measurement": mobile_review.evaluate(MEASURE),
            "coarsePointer": mobile_review.evaluate('matchMedia("(pointer:coarse)").matches'),
            "draft": mobile_review.locator("[data-conversation-input]").input_value(),
            "typeInsteadSucceeded": True,
        }
        mobile_page.screenshot(path=str(args.output / "managed-mobile-keyboard.png"))
        mobile.close()
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        (args.output / "preview-browser-evidence.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        # Inline adapters require no network request from the opaque frames.
        # Managed-file delivery occurs in the application frame before these frames mount.
        assert not result["previewRequests"], result["previewRequests"]
        assert not result["pageErrors"], result["pageErrors"]
        browser.close()
    print(
        json.dumps(
            {
                "cases": len(result["cases"]),
                "pageErrors": result["pageErrors"],
                "elapsedSeconds": result["elapsedSeconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
