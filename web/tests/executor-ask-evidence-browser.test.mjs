/** Exact Mac ask evidence controls both interactive affordances and handlers.
 * The static host mocks reads and the tail; no Panels server or live Mac participates.
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
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-executor-asks-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `executor-asks-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `executor-asks-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `executor-asks-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(hostPath, String.raw`<script lang="ts">
import LiveConversation from "../src/components/conversation/LiveConversation.svelte";
let conversationState = $state<"rest" | "peeked" | "opened">("opened");
</script>
<LiveConversation conversationId="conversation" persistenceKey="fixture" label="Worker" locale="macos" bind:conversationState sendMessage={async () => ({ conversation_id: "conversation", fate: "started" })} />
`, "utf8");
  await writeFile(mainPath, String.raw`import { mount } from "svelte";
import Host from "./HOST_NAME";
import { queryClient } from "../src/lib/queryClient";
let generation = 4;
let reachability = "reachable";
let kind = "permission";
let answer: any = null;
let applied = false;
let hold = false;
let finish: (() => void) | null = null;
const child = {executor_incarnation: "mac", child_id: "child", conversation_id: "conversation"};
const turn = {conversation_id: "conversation", turn_number: 1};
const options = [{option_id:"yes",label:"Yes",option_kind:"choice"}];
const questions = [{question_id:"q1",header:"First",question:"Choose first",options:[{label:"One",description:""}],multi_select:false,allow_other:true},{question_id:"q2",header:"Second",question:"Choose second",options:[{label:"Two",description:""}],multi_select:false,allow_other:true}];
let events: any[] = [{conversation_id:"conversation",sequence:1,kind:"prompt",payload:{text:"Work",sender_label:"owner",mode:"queue"},created_at:1},{conversation_id:"conversation",sequence:2,kind:"permission_asked",payload:{ask_id:"ask",title:"Allow?",detail:null,options},created_at:1}];
const evidence = () => ({ask:{child,turn,ask_id:kind === "permission" ? "ask" : "questions",kind},connection_generation:generation,reachability,observed_at_ms:10,answer_id:answer?.answer_id ?? null,applied_at_mac_ms:applied ? 20 : null});
const snapshot = () => ({conversation_id:"conversation",backend_key:"codex",supports_steer:false,model:null,reasoning_effort:null,workspace_folder:"/workspace",access:"full",role_text:null,identity_environment_variable_names:[],latest_sequence:events.length,owner_read_through_sequence:0,is_running:true,held_prompts:[],composer_catalog:[],executor_context:{child,turn},executor_asks:[{evidence:evidence(),answer}],pending_permission_ask:kind === "permission" ? {ask_id:"ask",title:"Allow?",detail:null,options,answer_available:reachability === "reachable" && answer === null,executor_evidence:evidence()}:null,pending_user_input:kind === "user_input" ? {request_id:"questions",questions,answer_available:reachability === "reachable" && answer === null,executor_evidence:evidence()}:null});
let health: any = {mode:"remote",identity:{executor_incarnation:"mac",connection_generation:4},connection_state:"ready",reconciliation_ready:true,last_contact_at_ms:100,last_contact_source:"vps_received_valid_executor_frame"};
(window as any).__posts = [];
(window as any).__setHealth = (next: any) => {health={...health,...next};queryClient.setQueryData(["executor-health"],health);};
(window as any).__evidence = async (next: any) => {if(next.generation !== undefined) generation=next.generation;if(next.reachability !== undefined) reachability=next.reachability;if(next.applied !== undefined) applied=next.applied;await queryClient.invalidateQueries({queryKey:["executor-health"]});};
(window as any).__hold = () => {hold=true;};
(window as any).__release = () => {hold=false;finish?.();};
(window as any).__questions = async () => {kind="user_input";answer=null;events.push({conversation_id:"conversation",sequence:3,kind:"permission_answered",payload:{ask_id:"ask",option_id:"yes"},created_at:1},{conversation_id:"conversation",sequence:4,kind:"user_input_requested",payload:{request_id:"questions",questions},created_at:1});for(const row of events.slice(2)) source?.emit("conversation-event",row);await queryClient.invalidateQueries({queryKey:["executor-health"]});};
(globalThis as any).fetch = async (input: any, init?: RequestInit) => {
 const url=String(input);
 if(url === "/api/executor/health") return Response.json(health);
 if(url.endsWith("/owner-read")) return Response.json({owner_read_through_sequence:0});
 if(url.includes("/events?after=")) return Response.json({events});
 if(url.endsWith("/permission-answers") || url.endsWith("/user-input-answers")) {
  (window as any).__posts.push(JSON.parse(String(init?.body)));
  answer={answer_id:"answer",ask:evidence().ask,accepted_at_vps_ms:15};
  return Response.json({landed:true});
 }
 if(url.endsWith("/conversations/conversation")) {if(hold) await new Promise<void>((resolve)=>finish=resolve);return Response.json(snapshot());}
 throw new Error("Unexpected request "+url);
};
let source: FakeEventSource | null = null;
class FakeEventSource {
 listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();
 constructor() {source=this;}
 addEventListener(type: string, fn: (event: MessageEvent<string>)=>void) {this.listeners.set(type,[...(this.listeners.get(type)??[]),fn]);}
 emit(type: string, data: unknown) {for(const fn of this.listeners.get(type)??[]) fn(new MessageEvent(type,{data:JSON.stringify(data)}));}
 close() {}
}
(globalThis as any).EventSource=FakeEventSource;
mount(Host,{target:document.getElementById("app")!});
`.replace("HOST_NAME", hostPath.split("/").at(-1)), "utf8");
  await writeFile(indexPath, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`, "utf8");
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

  const browserScript = String.raw`from playwright.sync_api import sync_playwright, expect
import sys
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width":1100,"height":800})
    page.set_default_timeout(5000)
    page.goto(sys.argv[1], wait_until="domcontentloaded")
    allow = page.locator('[data-conversation-ask-choice=yes]')
    expect(allow).to_be_enabled()
    page.evaluate("window.__setHealth({connection_state:'disconnected',reconciliation_ready:false})")
    expect(allow).to_be_disabled()
    page.evaluate("document.querySelector('[data-conversation-ask-choice=yes]').dispatchEvent(new Event('click',{bubbles:true}))")
    page.keyboard.press("1")
    assert page.evaluate("window.__posts.length") == 0
    # A newly ready connection cannot attest a request in an older generation.
    page.evaluate("window.__setHealth({connection_state:'ready',reconciliation_ready:true,identity:{executor_incarnation:'mac',connection_generation:5}})")
    expect(allow).to_be_disabled()
    # Even matching evidence cannot enable a control before its snapshot arrives.
    page.evaluate("window.__hold(); void window.__evidence({generation:5})")
    expect(allow).to_be_disabled()
    page.evaluate("window.__release()")
    expect(allow).to_be_enabled()
    page.keyboard.press("1")
    expect(allow).to_be_disabled()
    expect(page.locator('[data-conversation-ask-note]')).to_contain_text('Answer recorded on VPS')
    page.evaluate("window.__setHealth({connection_state:'disconnected',reconciliation_ready:false})")
    expect(page.locator('[data-conversation-ask-note]')).to_contain_text('Answer recorded on VPS')
    page.evaluate("window.__setHealth({connection_state:'ready',reconciliation_ready:true})")
    page.evaluate("window.__evidence({applied:true})")
    expect(page.locator('[data-conversation-ask-note]')).to_have_text('Answer applied on Mac.')
    page.evaluate("window.__questions()")
    one = page.locator('[data-user-input-option="One"]')
    expect(one).to_be_enabled()
    one.click()
    page.locator('[data-user-input-continue]').click()
    page.locator('[data-user-input-option="Two"]').click()
    # Keep the multi-question draft but require exact evidence before submit.
    page.evaluate("window.__evidence({reachability:'uncertain'})")
    submit = page.locator('[data-user-input-continue]')
    expect(submit).to_be_disabled()
    page.evaluate("document.querySelector('[data-user-input-continue]').dispatchEvent(new Event('click',{bubbles:true}))")
    assert page.evaluate("window.__posts.length") == 1
    page.evaluate("window.__evidence({reachability:'reachable'})")
    expect(submit).to_be_enabled()
    submit.click()
    expect(submit).to_be_disabled()
    assert page.evaluate("window.__posts[1].answers") == {"q1":{"answers":["One"]},"q2":{"answers":["Two"]}}
    browser.close()
print("executor-ask-evidence-browser.test.mjs: all assertions passed")
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
  assert.match(output, /executor-ask-evidence-browser\.test\.mjs: all assertions passed/);
  console.log("executor-ask-evidence-browser.test.mjs: all assertions passed");
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
