import json
from playwright.sync_api import sync_playwright
STATE = """() => { const t=document.querySelector('[data-conversation-thread]');
  return {scrollTop:Math.round(t.scrollTop), fromBottom:Math.round(t.scrollHeight-t.clientHeight-t.scrollTop),
          scrollHeight:Math.round(t.scrollHeight), jump:!!document.querySelector('.chat-jump')}; }"""
with sync_playwright() as p:
    b=p.chromium.launch(headless=True); c=b.new_context(viewport={"width":1440,"height":900}); pg=c.new_page()
    pg.goto("http://127.0.0.1:8767/#/workspace/chief-of-staff", wait_until="load")
    pg.wait_for_selector("[data-conversation-input]", timeout=60000); pg.wait_for_timeout(4000)
    pg.click("[data-conversation-input]"); pg.wait_for_selector("[data-conversation-thread]", timeout=60000)
    pg.wait_for_timeout(2000)
    if pg.query_selector("[data-conversation-expand]"): pg.click("[data-conversation-expand]")
    pg.wait_for_timeout(9000)
    box = pg.eval_on_selector("[data-conversation-thread]", "n=>{const r=n.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}}")
    out=[]
    for trial, trailing_wheel in ((0, False), (1, True), (2, True), (3, False), (4, True)):
        pg.mouse.move(box["x"], box["y"])
        for _ in range(14):
            pg.mouse.wheel(0,-420); pg.wait_for_timeout(90)
        pg.wait_for_timeout(2000)
        before = pg.evaluate(STATE)
        if pg.query_selector(".chat-jump") is None:
            out.append({"trial":trial,"note":"no jump control offered","before":before}); continue
        pg.click(".chat-jump")
        if trailing_wheel:
            # Inertia from the trackpad still arriving as the control is pressed.
            for _ in range(3):
                pg.mouse.wheel(0,-120); pg.wait_for_timeout(30)
        pg.wait_for_timeout(6000)
        after = pg.evaluate(STATE)
        out.append({"trial":trial,"trailing_wheel":trailing_wheel,"before":before,"after":after,
                    "landed_at_bottom": after["fromBottom"] <= 2})
    print(json.dumps(out, indent=2))
    c.close(); b.close()
