"""Use the conversation the way a person does, and keep what it looked like.

Usage: dogfood.py OUTPUT_DIRECTORY [BASE_URL]
"""
import json, pathlib, subprocess, sys, time
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
PROBE = (HERE / "probe.js").read_text()
WORDS = "Half a thought I have not finished and have not sent. It must survive everything."

STATE = """() => {
  const t = document.querySelector('[data-conversation-thread]');
  const i = document.querySelector('[data-conversation-input]');
  return {
    thread: !!t,
    rows: t ? t.querySelectorAll('[data-conversation-view-key]').length : 0,
    filled: t ? [...t.querySelectorAll('[data-conversation-view-key]')].filter(n => n.children.length > 0).length : 0,
    scrollTop: t ? Math.round(t.scrollTop) : null,
    scrollHeight: t ? Math.round(t.scrollHeight) : null,
    fromBottom: t ? Math.round(t.scrollHeight - t.clientHeight - t.scrollTop) : null,
    typed: i ? i.value : null,
    jump: !!document.querySelector('.chat-jump'),
    skeleton: !!document.querySelector('.skeleton'),
    spacer: (() => { const s = document.querySelector('[data-conversation-reserved-space]');
                     return s ? Math.round(s.getBoundingClientRect().height) : null; })()
  };
}"""


def main():
    out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8831"
    notes = []
    shot = [0]

    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 950})
        context.add_init_script(PROBE)
        page = context.new_page()

        def look(label):
            shot[0] += 1
            name = f"{shot[0]:02d}-{label}.png"
            page.screenshot(path=str(out / name))
            state = page.evaluate(STATE)
            notes.append({"step": label, "shot": name, **state})
            return state

        def hover():
            box = page.eval_on_selector("[data-conversation-thread]",
                "n => { const r = n.getBoundingClientRect(); return {x: r.x + r.width/2, y: r.y + r.height/2}; }")
            page.mouse.move(box["x"], box["y"])

        page.goto(base + "/#/workspace/chief-of-staff", wait_until="load")
        page.wait_for_selector("[data-conversation-input]", timeout=90000)
        page.wait_for_timeout(4000)
        page.click("[data-conversation-input]")
        page.wait_for_selector("[data-conversation-thread]", timeout=90000)
        page.wait_for_timeout(2000)
        if page.query_selector("[data-conversation-expand]"): page.click("[data-conversation-expand]")
        page.wait_for_timeout(9000)
        look("opened")

        page.click("[data-conversation-input]")
        page.type("[data-conversation-input]", WORDS, delay=18)
        page.wait_for_timeout(600)
        look("typed-and-unsent")

        # Read backwards the way you do when you are looking for something.
        hover()
        for _ in range(18): page.mouse.wheel(0, -420); page.wait_for_timeout(55)
        page.wait_for_timeout(2200)
        look("flicked-back")
        for _ in range(10): page.mouse.wheel(0, -260); page.wait_for_timeout(210)
        page.wait_for_timeout(1800)
        look("read-back-slowly")

        # The keyboard is a reader too.
        page.click("[data-conversation-thread]", position={"x": 20, "y": 20})
        for _ in range(4): page.keyboard.press("PageUp"); page.wait_for_timeout(350)
        page.wait_for_timeout(1800)
        look("paged-up")
        page.keyboard.press("End"); page.wait_for_timeout(2500)
        look("pressed-end")

        # Back up, then the control that says it takes you to the newest line.
        hover()
        for _ in range(16): page.mouse.wheel(0, -420); page.wait_for_timeout(70)
        for d in (240, 160, 110, 70, 40): page.mouse.wheel(0, -d); page.wait_for_timeout(30)
        page.wait_for_timeout(2500)
        look("away-from-the-newest-line")
        if page.query_selector(".chat-jump"): page.click(".chat-jump")
        page.wait_for_timeout(5000)
        look("after-latest")

        # Away to another screen and back, which is where he saw the skeleton.
        page.evaluate("location.hash = '#/workspace/utility/ideas'")
        page.wait_for_timeout(2500)
        look("another-screen")
        page.evaluate("location.hash = '#/workspace/chief-of-staff'")
        page.wait_for_timeout(1200)
        look("coming-back")
        if page.query_selector("[data-conversation-input]"): page.click("[data-conversation-input]")
        page.wait_for_timeout(4000)
        look("back-in-the-conversation")

        # Send something, which is the only way the answer room appears.
        page.click("[data-conversation-input]")
        page.fill("[data-conversation-input]", "")
        page.type("[data-conversation-input]", "Checking how a sent message settles.", delay=12)
        page.wait_for_timeout(400)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3500)
        look("just-sent")
        page.wait_for_timeout(6000)
        look("after-the-send-settled")

        # Put the unsent words back, then take the server away underneath them.
        page.click("[data-conversation-input]")
        page.type("[data-conversation-input]", WORDS, delay=10)
        page.wait_for_timeout(800)
        look("unsent-again")
        subprocess.run(["bash", "-c", f"kill $(cat {pathlib.Path.cwd()}/data/probe/server.pid)"], check=False)
        page.wait_for_timeout(9000)
        look("server-gone")
        subprocess.run(["bash", "-c", str(HERE / "restart-server.sh")], check=False)
        page.wait_for_timeout(16000)
        look("server-back")

        page.reload(wait_until="load")
        page.wait_for_timeout(6000)
        if page.query_selector("[data-conversation-input]"): page.click("[data-conversation-input]")
        page.wait_for_timeout(4000)
        look("after-a-reload")

        (out / "notes.json").write_text(json.dumps(notes, indent=2))
        for n in notes:
            print(f"{n['shot']:34s} rows={n['rows']:>3}/{n['filled']:<3} fromBottom={str(n['fromBottom']):>6} "
                  f"jump={str(n['jump']):5s} skeleton={str(n['skeleton']):5s} spacer={str(n['spacer']):>5} "
                  f"typed={'yes' if n['typed'] else ('empty' if n['typed'] == '' else 'none')}")
        context.close(); browser.close()


if __name__ == "__main__":
    main()
