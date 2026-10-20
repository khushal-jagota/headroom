import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `conversation-layer-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `conversation-layer-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `conversation-layer-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import { tick } from "svelte";
  import LiveConversation from "../src/components/conversation/LiveConversation.svelte";
  import {installConversationViewReaderFixture} from "../tests/support/conversationViewReaderFixture";
  const readerOnly=new URLSearchParams(window.location.search).has("reader");
  let readerState=$state<"opened"|"rest">("opened");
  if(readerOnly)installConversationViewReaderFixture();
  (window as any).__readerState=(state:typeof readerState)=>readerState=state;

  import MarkdownBlock from "../src/components/MarkdownBlock.svelte";
  import FilePreview from "../src/components/FilePreview.svelte";
  import { targetFromHref } from "../src/lib/filePreview";
  const previewsOnly=new URLSearchParams(window.location.search).has("previews");
  let previewMode=$state("media");
  let containerRevision=$state(0);
  const containerMarkdown='| Name | Detail | Outcome |\n| --- | --- | --- |\n'+Array.from({length:49},(_,i)=>'| Row '+i+' | A representative exact table cell with text | Preserved |').join('\n')+'\n\n'+Array.from({length:54},(_,i)=>(i+1)+'. A representative ordered-list item retains its exact numbering.').join('\n');
  (window as any).__containerRevision=()=>{containerRevision+=1;previewMode='containers';};
  const largeMarkdown=Array.from({length:1600},(_,index)=>"## Paragraph "+index+"\n\n"+"This exact logical document stays readable while the parser runs away from input. ".repeat(3)).join("\n\n");
  (window as any).__previewMode=(mode:string)=>{previewMode=mode;};
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import ConversationHistoryPicker from "../src/components/conversation/ConversationHistoryPicker.svelte";
  import ArtifactsBand from "../src/components/ArtifactsBand.svelte";
  const catalogsOnly=new URLSearchParams(window.location.search).has("catalogs");
  const catalogClient=new QueryClient({defaultOptions:{queries:{retry:false}}});
  let catalogSelected=$state<string|null>(null);
  let catalogOwner=$state("t_catalog");
  (window as any).__catalogOwner=(owner:string)=>catalogOwner=owner;
  import OrchestratorWorkspace from "../src/components/OrchestratorWorkspace.svelte";
  import { isPrimaryPressOutsideConversation } from "../src/lib/conversation/dismissal";
  const ticketGestures = new URLSearchParams(window.location.search).has("ticket");
  import ConversationPane from "../src/components/conversation/ConversationPane.svelte";
  import { discardHeldPrompt, promoteHeldPrompt } from "../src/lib/conversation/wire";
  import type { ConversationState } from "../src/lib/conversation/conversationState";

  const words = (index: number) =>
    ("Line " + index + ": the browser keeps this distinct paragraph anchored while the conversation changes height. ").repeat(3);
  let rows = $state<any[]>(
    Array.from({ length: 28 }, (_, index) => ({
      key: "message-" + index,
      kind: "agent_message",
      sequence: index + 1,
      createdAt: 1_000 + index,
      content: [{ piece: "text", text: words(index) }]
    }))
  );
  let visibleRows = $state<any[] | null>(null);
  let nextRowIndex = 28;
  let scrollReplacementGeneration = 0;
  let running = $state(false);
  let ownerReadThroughSequence = $state(0);
  let heldPromptRows = $state<any[]>([]);
  let supportsSteer = $state(false);
  let outgoingMessages = $state<any[]>([]);
  let composerDisabled = $state(false);
  let ask = $state<any>(null);

  // The connected test proves real writers and automatic SSE. This former E2E
  // matrix keeps its mock responses, exact request modes, and visibility refresh.
  let queueMatrixEnabled = $state(false);
  async function refreshQueueMatrix(): Promise<void> {
    if (!queueMatrixEnabled) return;
    const response = await fetch("/api/test/queue-matrix");
    heldPromptRows = await response.json();
  }
  async function actOnQueueMatrix(heldPromptId: string, mode: "discard" | "steer" | "send_now"): Promise<void> {
    if (mode === "discard") await discardHeldPrompt("browser-fixture", heldPromptId);
    else await promoteHeldPrompt("browser-fixture", heldPromptId, mode);
    await refreshQueueMatrix();
  }
  document.addEventListener("visibilitychange", () => { void refreshQueueMatrix(); });
  (window as any).__loadQueueMatrix = async () => {
    queueMatrixEnabled = true;
    supportsSteer = true;
    running = true;
    conversationState = "opened";
    await refreshQueueMatrix();
  };

  // Enough command entries to fill the menu, and enough shared letters to narrow it.
  const composerCatalog = [
    { kind: "command", display_text: "/recap", insertion_text: "/recap ",
      description: "Recap the ticket", argument_hint: null },
    { kind: "command", display_text: "/release", insertion_text: "/release ",
      description: "Cut a release", argument_hint: null },
    { kind: "command", display_text: "/rename", insertion_text: "/rename ",
      description: "Rename the thing", argument_hint: null },
    { kind: "command", display_text: "/reset", insertion_text: "/reset ",
      description: "Reset local state", argument_hint: null },
    { kind: "command", display_text: "/review", insertion_text: "/review ",
      description: "Review the current diff", argument_hint: null },
    { kind: "command", display_text: "/start", insertion_text: "/start ",
      description: "Start the worker", argument_hint: null },
    { kind: "command", display_text: "/status", insertion_text: "/status ",
      description: "Show the status", argument_hint: null },
    { kind: "command", display_text: "/stop", insertion_text: "/stop ",
      description: "Stop the worker", argument_hint: null }
  ];
  let conversationState = $state<ConversationState>("rest");
  let lens = $state<"focus" | "full">("focus");

  async function captureSend(content: any[], mode: string, picked: any): Promise<boolean> {
    (window as any).__sentModes = [...((window as any).__sentModes ?? []), mode];
    (window as any).__sentRuns = [...((window as any).__sentRuns ?? []), picked];
    heldPromptRows = [{
      key: "composer-fallback",
      heldPromptId: "composer-fallback",
      content,
      state: "held",
      queueReason: picked.model === null ? "steer_refused" : "run_change"
    }];
    return true;
  }

  // The Ticket layout uses the same production predicate as TicketRoute. The
  // orchestrator below mounts its actual production workspace and handlers.
  function dismissTicketOnPointerDown(event: PointerEvent): void {
    if (isPrimaryPressOutsideConversation(event)) conversationState = "rest";
  }
  (window as any).__setConversationState = (state: ConversationState) => conversationState = state;

  (window as any).__appendConversationLine = () => {
    const index = nextRowIndex++;
    rows = [
      ...rows,
      {
        key: "message-" + index,
        kind: "agent_message",
        sequence: index + 1,
        createdAt: 1_000 + index,
        content: [{ piece: "text", text: "Newest appended line " + index }]
      }
    ];
  };
  // Controlled content replay. Responses stay pending while the reader chooses a
  // position, then browser-owned image/Markdown completion changes the same rows.
  (window as any).__setupScrollRegression = (kind: string, mode = "scrollback") => {
    conversationState = "peeked";
    lens = "full";
    running = false;
    const paragraphs = Array.from({ length: 32 }, (_, index) =>
      "Tall paragraph " + index + ": " + words(index));
    const previewAt = mode === "following" ? 30 : 14;
    rows = Array.from({ length: 28 }, (_, index) => ({
      key: "regression-" + index,
      kind: "agent_message",
      sequence: index + 1,
      createdAt: 1_000 + index,
      content: index === (mode === "following" ? 27 : 12) && kind !== "plain"
        ? [
            {piece: "text", text: paragraphs.slice(0, previewAt).join("\n\n")},
            kind === "image"
              ? {piece: "image", stored_file_id: "delayed.png", file_name: "delayed.png"}
              : {piece: "text", text: "[Delayed Markdown](/files/tickets/t_scrollregression/delayed.md)"},
            {piece: "text", text: paragraphs.slice(previewAt).join("\n\n")}
          ]
        : [{piece: "text", text: words(index)}]
    }));
    visibleRows = null;
  };
  (window as any).__replaceAllScrollRows = (count = rows.length) => {
    scrollReplacementGeneration += 1;
    rows = rows.slice(0, count).map((row, index) => ({...row, key: "replacement-" + scrollReplacementGeneration + "-" + index,
      content: [{piece: "text", text: "Replacement " + words(index)}]}));
  };
  (window as any).__sendScrollRegression = (moveLayer: boolean) => {
    outgoingMessages = [{messageId: "scroll-send", content: [{piece: "text", text: "Regression prompt"}],
      senderLabel: "owner", mode: "queue", sentAtUnixMilliseconds: 20_000, knownFate: "nothing_yet"}];
    if (moveLayer) conversationState = "opened";
  };
  (window as any).__answerScrollRegression = (paragraphCount: number) => {
    const prompt = {key: "scroll-prompt", kind: "prompt", sequence: 1_000, createdAt: 20_000,
      content: [{piece: "text", text: "Regression prompt"}], senderLabel: "owner", mode: "queue",
      senderMessageId: "scroll-send", sentAtUnixMilliseconds: 20_000};
    rows = [...rows.filter(row => row.sequence < 1_000), prompt,
      {key: "scroll-reply", kind: "agent_message", sequence: 1_001, createdAt: 20_001,
        content: [{piece: "text", text: Array.from({length:paragraphCount},(_,index) => words(index)).join("\n\n")}]}];
    outgoingMessages = [];
  };
  (window as any).__proseDelta = (index: number) => {
    rows = rows.map((row, at) => at === rows.length - 1
      ? {...row, content: [{piece: "text", text: row.content[0].text + "\n\nControlled prose delta " + index + ": " + words(index)}]}
      : row);
  };
  (window as any).__dropFirstConversationLines = (count: number) => {
    rows = rows.slice(count);
  };
  (window as any).__shortenFirstConversationLine = () => {
    rows = rows.map((row, index) => index === 0
      ? {...row, content: [{piece:"text", text:"A short message."}]} : row);
  };
  (window as any).__growFirstConversationLine = () => {
    rows = rows.map((row, index) =>
      index === 0
        ? {
            ...row,
            content: [{
              piece: "text",
              text: ("This existing line became much taller above the reader. ").repeat(45)
            }]
          }
        : row
    );
  };
  (window as any).__rememberConversationInput = () => {
    (window as any).__conversationInput = document.querySelector("[data-conversation-input]");
  };
  (window as any).__conversationInputSurvived = () =>
    (window as any).__conversationInput === document.querySelector("[data-conversation-input]");
  (window as any).__showActivePlan = () => {
    rows = [
      ...rows,
      {
        key: "active-prompt",
        kind: "prompt",
        sequence: 1_000,
        createdAt: 2_000,
        content: [{ piece: "text", text: "Continue" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 2_000
      },
      {
        key: "active-plan",
        kind: "plan_updated",
        sequence: 1_001,
        createdAt: 2_001,
        entries: [
          { text: "Inspect", status: "completed" },
          { text: "Implement", status: "in_progress" },
          { text: "Verify", status: "pending" }
        ]
      }
    ];
    running = true;
  };
  (window as any).__showHeldPrompt = (supported: boolean) => {
    supportsSteer = supported;
    heldPromptRows = [{
      key: "held-1",
      heldPromptId: "held-1",
      content: [{ piece: "text", text: "waiting guidance" }],
      state: "held",
      queueReason: "steer_refused"
    }];
  };
  (window as any).__showUnansweredSend = () => {
    supportsSteer = true;
    heldPromptRows = [{
      key: "local:sent-into-the-dark",
      heldPromptId: null,
      senderMessageId: "sent-into-the-dark",
      content: [{ piece: "text", text: "did this arrive" }],
      senderLabel: "owner",
      sentAtUnixMilliseconds: 10,
      state: "unknown",
      queueReason: null
    }];
  };
  (window as any).__setComposerDisabled = (disabled: boolean) => {
    composerDisabled = disabled;
  };
  (window as any).__settle = async () => {
    await tick();
    await tick();
  };
  (window as any).__showEveryMark = () => {
    ownerReadThroughSequence = 3_000;
    running = true;
    rows = [
      {
        key: "mark-reply",
        kind: "agent_message",
        sequence: 3_001,
        createdAt: 4_000,
        content: [{ piece: "text", text: "the reply nobody has read" }],
        toOwner: true
      },
      {
        key: "mark-end",
        kind: "turn_ended",
        sequence: 3_002,
        createdAt: 4_001,
        ending: "failed",
        errorSummary: "backend exited",
        automaticCompactionResult: null
      }
    ];
    visibleRows = rows;
  };
  (window as any).__showQuietComposer = () => {
    running = false;
    rows = [];
    visibleRows = rows;
  };
  (window as any).__showWorkRunning = () => {
    running = true;
  };
  (window as any).__showRunningAndNeedsYou = () => {
    ownerReadThroughSequence = 3_000;
    running = true;
    (window as any).__permissionAnswers = [];
    ask = {
      askId: "ask-mark",
      title: "may I run this",
      detail: "Write the permission exercise file",
      options: [
        { option_id: "allow-file", label: "Allow once", option_kind: "allow_once" },
        { option_id: "deny-file", label: "Deny once", option_kind: "reject_once" }
      ]
    };
    rows = [
      {
        key: "mark-ask",
        kind: "permission_ask",
        sequence: 3_020,
        createdAt: 4_020,
        askId: "ask-mark",
        title: "may I run this",
        detail: null,
        state: "live",
        deadReason: null,
        chosenOptionId: null
      }
    ];
    visibleRows = rows;
  };
  (window as any).__showOrdinaryOutputOnly = () => {
    ownerReadThroughSequence = 3_000;
    running = false;
    ask = null;
    rows = [
      {
        key: "chatter",
        kind: "agent_message",
        sequence: 3_010,
        createdAt: 4_010,
        content: [{ piece: "text", text: "ordinary output, not a reply" }],
        toOwner: false
      }
    ];
    visibleRows = rows;
  };
  (window as any).__showSettledFocusRestLine = () => {
    rows = [
      {
        key: "settled-prompt",
        kind: "prompt",
        sequence: 2_000,
        createdAt: 3_000,
        content: [{ piece: "text", text: "finish this" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 3_000
      },
      {
        key: "settled-reply",
        kind: "agent_message",
        sequence: 2_001,
        createdAt: 3_001,
        content: [{ piece: "text", text: "settled reply" }]
      },
      {
        key: "settled-end",
        kind: "turn_ended",
        sequence: 2_002,
        createdAt: 3_002,
        ending: "completed",
        errorSummary: null,
        automaticCompactionResult: null
      }
    ];
    visibleRows = rows.slice(0, -1);
    lens = "focus";
    running = false;
    conversationState = "rest";
  };
  (window as any).__showSettledTurn = () => {
    const tool = (index: number) => ({
      key: "settled-tool-" + index,
      kind: "tool_call",
      sequence: 3_100 + index,
      createdAt: 4_100 + index,
      toolCallId: "settled-tool-" + index,
      title: "Tool " + index,
      toolKind: "read",
      detail: null,
      startedDetail: null,
      status: "completed",
      progress: null
    });
    rows = [
      {
        key: "full-prompt",
        kind: "prompt",
        sequence: 3_000,
        createdAt: 4_000,
        content: [{ piece: "text", text: "show everything" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 4_000
      },
      {
        key: "full-commentary",
        kind: "agent_message",
        sequence: 3_001,
        createdAt: 4_001,
        content: [{ piece: "text", text: "hidden commentary" }]
      },
      {key:"owner-first",kind:"agent_message",sequence:3_002,createdAt:4_002,
        content:[{piece:"text",text:"Owner answer one."}],toOwner:true},
      {key:"middle-commentary",kind:"agent_message",sequence:3_003,createdAt:4_003,
        content:[{piece:"text",text:"middle commentary"}],toOwner:false},
      {key:"owner-second",kind:"agent_message",sequence:3_004,createdAt:4_004,
        content:[{piece:"text",text:"Owner answer two."}],toOwner:true},
      ...[1, 2, 3, 4].map(tool),
      {
        key: "full-answer",
        kind: "agent_message",
        sequence: 3_200,
        createdAt: 4_200,
        content: [{ piece: "text", text: "final answer" }],
        toOwner: true
      },
      {
        key: "full-end",
        kind: "turn_ended",
        sequence: 3_201,
        createdAt: 4_201,
        ending: "completed",
        errorSummary: null,
        automaticCompactionResult: null
      }
    ];
    visibleRows = rows.filter(row => row.kind === "prompt" || (row.kind === "agent_message" && row.toOwner === true));
    lens = "focus";
    conversationState = "opened";
  };
  (window as any).__showTurnEndings = () => {
    rows = [
      {
        key: "failed-prompt",
        kind: "prompt",
        sequence: 4_000,
        createdAt: 5_000,
        content: [{ piece: "text", text: "run the backend" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 5_000
      },
      {
        key: "failed-end",
        kind: "turn_ended",
        sequence: 4_001,
        createdAt: 5_001,
        ending: "failed",
        errorSummary: "backend exited",
        automaticCompactionResult: null
      },
      {
        key: "lost-prompt",
        kind: "prompt",
        sequence: 4_002,
        createdAt: 5_002,
        content: [{ piece: "text", text: "turn that loses its ending" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 5_002
      },
      {
        key: "turn-stopped",
        kind: "turn_stopped",
        sequence: 4_003,
        createdAt: 5_003
      }
    ];
    visibleRows = null;
    lens = "full";
    running = false;
    conversationState = "opened";
  };
</script>

{#if readerOnly}
  <div style="height:700px;width:700px;display:flex;flex-direction:column">
    <LiveConversation conversationId="bounded-reader" persistenceKey="bounded-reader" label="Worker" bind:conversationState={readerState} sendMessage={async()=>({conversation_id:"bounded-reader",fate:"started"})}/>
  </div>
{:else if previewsOnly}
  <input aria-label="Parser input" />
  <div data-conversation-thread data-preview-fixture style="height:600px;overflow:auto">
  {#if previewMode === "large"}
    <MarkdownBlock text={largeMarkdown} />
  {:else if previewMode === "copy"}
    <div data-copy-selection>
      <p data-copy-before>OUTSIDE BEFORE prefix KEEP START</p>
      <div data-copy-first data-conversation-thread style="height:160px;overflow:auto">
        <MarkdownBlock text={largeMarkdown.replaceAll('Paragraph ', 'FIRST Paragraph ')} />
      </div>
      <p data-copy-middle>OUTSIDE BETWEEN messages</p>
      <div data-copy-second data-conversation-thread style="height:160px;overflow:auto">
        <MarkdownBlock text={largeMarkdown.replaceAll('Paragraph ', 'SECOND Paragraph ')} />
      </div>
      <p data-copy-after>KEEP END suffix OUTSIDE AFTER</p>
    </div>
  {:else if previewMode === "containers"}
    <MarkdownBlock text={containerMarkdown+'\n\nRevision '+containerRevision} />
  {:else if previewMode === "media"}
    {#each Array.from({length:24},(_,index)=>index) as index}
      <div style="min-height:340px">
        <FilePreview target={targetFromHref('/files/tickets/t_preview/preview'+index+'.html')} />
        <FilePreview target={targetFromHref('/files/tickets/t_preview/image'+index+'.png')} />
      </div>
    {/each}
  {/if}
  </div>
{:else if catalogsOnly}
<QueryClientProvider client={catalogClient}>
  <section style="padding:40px;width:600px;" data-catalog-fixture>
    <ConversationHistoryPicker owner={{kind:"ticket",id:"t_catalog"}} bind:selected={catalogSelected} label="Prior conversation" conversationExists={true} />
    <output data-history-selection>{catalogSelected ?? "current"}</output>
    <ArtifactsBand owner={{kind:"ticket",id:catalogOwner}} />
  </section>
</QueryClientProvider>
{:else}
{#snippet conversationPane()}
      <ConversationPane
        bind:conversationState
        bind:lens
        conversationId="browser-fixture"
        conversationExists
        {rows}
        {visibleRows}
        {running}
        {ask}
        onAnswer={(optionId: string) => {
          (window as any).__permissionAnswers.push({ askId: ask.askId, optionId });
          ask = null;
        }}
        {ownerReadThroughSequence}
        {heldPromptRows}
        {supportsSteer}
        backendKey="claude"
        current={{ model: "opus", reasoningEffort: "high" }}
        models={[
          { model_id: "opus", display_name: "Opus", enabled: true },
          { model_id: "sonnet", display_name: "Sonnet", enabled: true,
            reasoning_effort_options: [] }
        ]}
        effortOptions={["high"]}
        {outgoingMessages}
        ownSenderLabel="owner"
        composerDisabled={composerDisabled}
        {composerCatalog}
        onSend={captureSend}
        onDiscardHeldPrompt={(heldPromptId: string) => actOnQueueMatrix(heldPromptId, "discard")}
        onPromoteHeldPrompt={(heldPromptId: string, mode: "steer" | "send_now") => actOnQueueMatrix(heldPromptId, mode)}
        onStopDrawingHeldPrompt={(senderMessageId: string) => {
          (window as any).__stoppedDrawing = senderMessageId;
        }}
        onSendHeldPromptAgain={(senderMessageId: string) => {
          (window as any).__sentAgain = senderMessageId;
        }}
      />
{/snippet}
<div class="fixture-ticket" class:ticket-screen={queueMatrixEnabled}>
  {#if ticketGestures}
    <main class="ticket-doc fixture-document" onpointerdowncapture={dismissTicketOnPointerDown}>
      <button type="button" data-ticket-behind>Ticket content</button>
    </main>
    <section class="conversation-layer" onpointerdowncapture={dismissTicketOnPointerDown}>
      <div class="conversation-column">{@render conversationPane()}</div>
    </section>
  {:else}
    <OrchestratorWorkspace bind:conversationState>
      <button type="button" data-ticket-behind>Ticket content</button>
      {#snippet conversation()}{@render conversationPane()}{/snippet}
    </OrchestratorWorkspace>
  {/if}
</div>

{/if}

<style>
  :global(html), :global(body), :global(#app) {
    height: 100%;
    margin: 0;
  }
  .fixture-ticket {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    height: 720px;
    padding: 16px;
  }
  .fixture-document {
    flex: 1;
    min-height: 0;
  }
  :global([data-ticket-behind]) {
    position: fixed;
    inset: 4px auto auto 4px;
    z-index: 3;
  }
  .fixture-ticket :global(.conversation-layer) {
    align-items: center;
  }
  .fixture-ticket:not(.ticket-screen) :global(.conversation-column) {
    box-sizing: border-box;
    width: min(680px, calc(100% - 96px));
  }
  .fixture-ticket.ticket-screen {
    height: 100%;
    padding: 0;
  }
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


  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./conversation-pane.py", import.meta.url))};
}
