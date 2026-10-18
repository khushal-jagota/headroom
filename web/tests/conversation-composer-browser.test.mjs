/**
 * Browser-only redesigned composer contracts.
 *
 * This builds a small Svelte host around the production ConversationComposer and serves
 * only static assets. No Panels server or API participates: layout and draft intake
 * are frontend contracts; the host controls only parent-owned state and send outcomes.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";
import { scratchDirectory } from "./support/scratch.mjs";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-composer-redesign-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `composer-redesign-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `composer-redesign-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `composer-redesign-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import ConversationComposer from "../src/components/conversation/ConversationComposer.svelte";
  let running = $state(false);
  let ask = $state<any>(null);
  let heldPromptRows = $state<any[]>([]);
  let errorNote = $state<string | null>(null);
  (window as any).__setState = (state: string) => {
    running = state === "running";
    ask = state === "ask" ? {askId:"permission",title:"Run the migration?",detail:"Copied database only",options:[
      {option_id:"deny",label:"Deny",option_kind:"reject_once"},
      {option_id:"approve",label:"Approve once",option_kind:"allow_once"}]} : null;
    heldPromptRows = state === "held" ? [{key:"held",heldPromptId:"held",content:[{piece:"text",text:"After that, show the diff"}],state:"held",queueReason:null}] : [];
    errorNote = state === "refused" ? "Message refused: connection lost" : null;
  };
  (window as any).__attempts = [];
  async function send(content: any[], mode: string, picked: any): Promise<boolean> {
    (window as any).__attempts.push({content,mode,picked});
    return new Promise(resolve => {
      (window as any).__resolveSend = (accepted: boolean) => {
        errorNote = accepted ? null : "Message refused: connection lost";
        resolve(accepted);
      };
    });
  }
</script>
<main data-conversation-pane style="--conversation-target:32px;--conversation-glyph:22px">
  <div class="thread">The conversation remains above the composer.</div>
  <ConversationComposer conversationId="fixture" conversationExists backendKey="claude"
    {running} {ask} {heldPromptRows} {errorNote} supportsSteer
    current={{model:"opus",reasoningEffort:"high"}}
    models={[{model_id:"opus",display_name:"Opus with a deliberately long model display name",enabled:true},{model_id:"sonnet",display_name:"Sonnet",enabled:true}]}
    effortOptions={["high"]} onSend={send} onStop={() => running = false} onAnswer={() => ask = null}
  />
</main>
<style>
  :global(html), :global(body), :global(#app) {height:100%;margin:0;}
  main {display:flex;flex-direction:column;width:100%;height:720px;min-height:0;overflow:hidden;}
  .thread {flex:1;min-height:0;overflow:auto;padding:16px;}
</style>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    `import "../../assets/tokens.css";\nimport "../../assets/app.css";\nimport { mount } from "svelte";\nimport Host from "./${hostPath.split("/").at(-1)}";\nmount(Host, { target: document.getElementById("app")! });\n`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );

  await build({
    root: webRoot,
    base: "./",
    configFile: false,
    logLevel: "silent",
    plugins: [svelte()],
    build: {
      emptyOutDir: true,
      outDir: temporaryDirectory,
      rollupOptions: { input: { index: indexPath } }
    }
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", temporaryDirectory],
    { cwd: repositoryRoot, stdio: "ignore" }
  );
  const builtIndex = (await readdir(temporaryDirectory, { recursive: true })).find((path) =>
    path.endsWith(".html")
  );
  assert.ok(builtIndex, "the component build must emit an HTML entry");
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import base64
import math
import sys

INPUT = "[data-conversation-input]"
ATTACHMENTS = "[data-chat-image-name], [data-chat-file-name]"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")

def settled(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

def geometry(page):
    result = page.locator(".chat-foot").evaluate("""foot => {
      const bounds = foot.getBoundingClientRect();
      const visible = e => e.getBoundingClientRect().width > 0;
      const buttons = [...foot.querySelectorAll('button')].filter(visible);
      return {overflow:foot.scrollWidth > foot.clientWidth + 1, buttons:buttons.map(button => {
        const b = button.getBoundingClientRect(), s = getComputedStyle(button);
        const glyph = button.querySelector('svg,.chat-send-fill');
        const g = glyph?.getBoundingClientRect();
        return {label:button.getAttribute('aria-label'),square:button.matches('.chat-image,.chat-send'),
          pill:button.matches('.listbox-picker-trigger'),w:b.width,h:b.height,cy:b.y+b.height/2,
          outside:b.left < bounds.left-1 || b.right > bounds.right+1,
          background:s.backgroundColor,border:s.borderTopWidth,stop:button.hasAttribute('data-conversation-stop'),glyph:g ? [g.width,g.height] : null,
          offset:g ? [g.x+g.width/2-b.x-b.width/2,g.y+g.height/2-b.y-b.height/2] : null};
      }),chevrons:[...foot.querySelectorAll('.listbox-picker-chevron,.listbox-picker-chevron-drawn')].filter(visible).length};
    }""")
    assert not result['overflow'], result
    assert result['chevrons'] == 0, result
    for b in result['buttons']:
        assert not b['outside'], b
        if b['square']:
            assert abs(b['w']-32) <= 0.1 and abs(b['h']-32) <= 0.1, b
            assert b['offset'] and max(abs(n) for n in b['offset']) <= 0.1, b
            assert b['glyph'] == ([11,11] if b['stop'] else [22,22]), b
        if b['pill']:
            assert abs(b['h']-32) <= 0.1, b
    centers = [b['cy'] for b in result['buttons']]
    assert max(centers)-min(centers) <= 1, result
    stop = page.locator('[data-conversation-stop]')
    send = page.locator('[data-conversation-send].on')
    if send.count():
        assert send.evaluate("e => {const probe=document.createElement('span');probe.style.color='var(--accent-bright)';e.append(probe);const accent=getComputedStyle(probe).color;probe.remove();return getComputedStyle(e).backgroundColor === accent}")
    if stop.count():
        style = stop.evaluate("e => ({bg:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderTopWidth})")
        assert style == {'bg':'rgba(0, 0, 0, 0)','border':'1px'}, style
    return result

def names(page):
    return page.locator(ATTACHMENTS).evaluate_all("els => els.map(e => e.dataset.chatImageName ?? e.dataset.chatFileName)")

def attach(page):
    page.locator('[data-conversation-file-input]').set_input_files([
      {'name':'first.png','mimeType':'image/png','buffer':PNG},
      {'name':'notes.md','mimeType':'text/markdown','buffer':b'notes'},
      {'name':'second.png','mimeType':'image/png','buffer':PNG},
      {'name':'run.txt','mimeType':'text/plain','buffer':b'log'}])
    page.wait_for_function("() => document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === 4")
    assert names(page) == ['first.png','notes.md','second.png','run.txt']

def height(page):
    return page.locator(INPUT).evaluate("e => ({h:e.getBoundingClientRect().height,line:parseFloat(getComputedStyle(e).lineHeight),scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY,padding:parseFloat(getComputedStyle(e).paddingTop)+parseFloat(getComputedStyle(e).paddingBottom)})")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':980,'height':800})
    page.set_default_timeout(5000)
    errors = []
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.on('console',lambda message: errors.append(message.text) if message.type == 'error' else None)
    page.context.grant_permissions(['clipboard-read','clipboard-write'])
    # Local browser capability is enough to render the microphone; never record.
    page.add_init_script("Object.defineProperty(navigator, 'mediaDevices', {value:{getUserMedia:async()=>{throw Error('not used')}}});")
    for width in [980,390]:
        page.set_viewport_size({'width':width,'height':800})
        for state in ['empty','typing','long','attachments','held','running','ask','refused']:
            page.goto(sys.argv[1],wait_until='domcontentloaded')
            page.evaluate('(state) => window.__setState(state)',state)
            if state in ['typing','held','running','refused']:
                page.locator(INPUT).fill('Run the migration against the copied database, then show the diff.')
            if state == 'long':
                page.locator(INPUT).fill('\n'.join('Line '+str(n) for n in range(30)))
            if state == 'attachments':
                attach(page)
            settled(page)
            geometry(page)
            if width == 390 and state == 'running':
                assert page.locator('.model-picker-face').evaluate("e => e.scrollWidth > e.clientWidth && getComputedStyle(e).textOverflow === 'ellipsis'")
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width,state)
            if state == 'empty':
                assert page.locator('[data-conversation-send]').count() == 0
            if state == 'attachments':
                boxes = page.locator(ATTACHMENTS).evaluate_all('els => els.map(e => e.getBoundingClientRect().top)')
                assert max(boxes)-min(boxes) <= 1, boxes
                page.locator('[data-chat-file-name="notes.md"] button').click()
                assert names(page) == ['first.png','second.png','run.txt']
            if state == 'held':
                assert page.locator('[data-conversation-held-stack]').is_visible()
            if state == 'ask':
                assert page.locator('[data-conversation-ask-action="approve"]').is_visible()
                assert page.locator('[data-conversation-add]').count() == 0
            if state == 'refused':
                assert page.locator('[data-conversation-error]').inner_text() == 'Message refused: connection lost'

    # Image-only, file-only and mixed drafts occupy one identical strip height.
    strip_heights = []
    for selected in [['picture.png'],['notes.md'],['picture.png','notes.md']]:
        page.goto(sys.argv[1])
        page.locator('[data-conversation-file-input]').set_input_files([
          {'name':name,'mimeType':'image/png' if name.endswith('.png') else 'text/markdown',
           'buffer':PNG if name.endswith('.png') else b'notes'} for name in selected])
        page.wait_for_function("count => document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === count",arg=len(selected))
        sizes = page.locator(ATTACHMENTS).evaluate_all('els => els.map(e => e.getBoundingClientRect().height)')
        assert sizes == [52]*len(selected), sizes
        strip_heights.append(page.locator('[data-conversation-attachments]').bounding_box()['height'])
    assert max(strip_heights)-min(strip_heights) <= 1, strip_heights

    # Content height follows typing and deletion, caps on a complete line, and scrolls there.
    page.goto(sys.argv[1])
    small = height(page)
    page.locator(INPUT).fill('one\ntwo\nthree\nfour')
    settled(page)
    medium = height(page)
    assert medium['h'] > small['h'] + medium['line']*2, (small,medium)
    page.locator(INPUT).fill('\n'.join('line '+str(n) for n in range(40)))
    settled(page)
    tall = height(page)
    cap = math.floor(min(720*.44,tall['line']*12)/tall['line'])*tall['line']
    assert abs(tall['h']-tall['padding']-cap) <= 1, (tall,cap)
    assert tall['overflow'] == 'auto' and tall['scroll'] > tall['h'], tall
    page.locator(INPUT).evaluate('e => e.scrollTop = e.scrollHeight')
    assert page.locator(INPUT).evaluate('e => e.scrollTop') > 0
    page.locator('[data-conversation-pane]').evaluate("e => e.style.height = '360px'")
    settled(page)
    shorter = height(page)
    assert shorter['h'] < tall['h'], (tall,shorter)
    page.locator(INPUT).fill('short')
    settled(page)
    assert height(page)['h'] <= small['h']+1
    assert height(page)['overflow'] == 'hidden'

    # Width changes remeasure wrapping without a keypress.
    page.locator('[data-conversation-pane]').evaluate("e => e.style.height = '720px'")
    page.set_viewport_size({'width':980,'height':800})
    page.locator(INPUT).fill('Words that wrap across the available composer width. '*7)
    settled(page)
    wide = height(page)
    page.set_viewport_size({'width':390,'height':800})
    settled(page)
    assert height(page)['h'] > wide['h'], (wide,height(page))

    # Native text paste grows the actual textarea; mixed drag/drop and image paste
    # travel through browser events into the same ordered attachment strip.
    page.goto(sys.argv[1])
    page.locator(INPUT).click()
    page.evaluate("navigator.clipboard.writeText('pasted one\\npasted two\\npasted three')")
    page.keyboard.press('Control+V')
    page.wait_for_function("document.querySelector('[data-conversation-input]').value.includes('pasted three')")
    settled(page)
    assert height(page)['h'] > small['h'] + small['line'], height(page)
    page.locator('[data-conversation-box]').evaluate("""box => {
      const data = new DataTransfer();
      data.items.add(new File(['dropped'], 'dropped.txt', {type:'text/plain'}));
      const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='),c=>c.charCodeAt(0));
      data.items.add(new File([bytes], 'dropped.png', {type:'image/png'}));
      box.dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:data}));
    }""")
    page.wait_for_function("document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === 2")
    assert names(page) == ['dropped.txt','dropped.png']
    page.locator(INPUT).evaluate("""input => {
      const data = new DataTransfer();
      const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='),c=>c.charCodeAt(0));
      data.items.add(new File([bytes], 'pasted.png', {type:'image/png'}));
      input.dispatchEvent(new ClipboardEvent('paste', {bubbles:true,cancelable:true,clipboardData:data}));
    }""")
    page.wait_for_function("document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === 3")
    assert names(page) == ['dropped.txt','dropped.png','pasted.png']
    geometry(page)

    # Refusal restores a single ordered draft; later composition wins over old sends.
    page.goto(sys.argv[1])
    attach(page)
    page.locator(INPUT).fill('original draft')
    page.locator(INPUT).press('Enter')
    page.wait_for_function('window.__attempts.length === 1')
    assert page.locator(INPUT).input_value() == ''
    assert names(page) == []
    page.evaluate('window.__resolveSend(false)')
    page.wait_for_function("document.querySelector('[data-conversation-input]').value === 'original draft'")
    assert names(page) == ['first.png','notes.md','second.png','run.txt']
    page.locator('[data-chat-image-name="first.png"] button').click()
    assert names(page) == ['notes.md','second.png','run.txt']
    page.locator(INPUT).press('Enter')
    page.wait_for_function('window.__attempts.length === 2')
    page.locator(INPUT).fill('newer draft')
    page.locator('[data-conversation-file-input]').set_input_files({'name':'new.txt','mimeType':'text/plain','buffer':b'new'})
    page.locator('[data-chat-file-name="new.txt"]').wait_for()
    page.evaluate('window.__resolveSend(false)')
    settled(page)
    assert page.locator(INPUT).input_value() == 'newer draft'
    assert names(page) == ['new.txt']
    assert not errors, errors
    browser.close()
print('conversation-composer-browser.test.mjs: all assertions passed')
`;
  const probe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] }
  );
  let output = "";
  probe.stdout.on("data", (chunk) => { output += chunk; });
  probe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => probe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /conversation-composer-browser\.test\.mjs: all assertions passed/);
  console.log("conversation-composer-browser.test.mjs: all assertions passed");
} finally {
  serverProcess?.kill();
  await rm(hostPath, { force: true });
  await rm(mainPath, { force: true });
  await rm(indexPath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
}

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`static component host did not become ready: ${url}`);
}
