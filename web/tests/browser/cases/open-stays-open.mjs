import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = "open-stays-open";
const written = [];

async function page(name, markup) {
  const hostName = `${stem}-${name}-${process.pid}`;
  await writeFile(join(scratchRoot, `${hostName}.svelte`), markup);
  await writeFile(
    join(scratchRoot, `${hostName}.ts`),
    `import { mount } from 'svelte'; import Host from './${hostName}.svelte';` +
      ` import '../../assets/tokens.css'; import '../../assets/app.css';` +
      ` mount(Host, { target: document.getElementById('app')! });`
  );
  await writeFile(
    join(scratchRoot, `${hostName}.html`),
    `<html><body><div id="app"></div><script type="module" src="./${hostName}.ts"></script></body></html>`
  );
  written.push(
    join(scratchRoot, `${hostName}.svelte`),
    join(scratchRoot, `${hostName}.ts`),
    join(scratchRoot, `${hostName}.html`)
  );
  return `.test-scratch/${hostName}.html`;
}

  // The thread, while an agent is working. Opening a tool call is the reader's hands; the
  // turn ending is the server's, and it folds the whole run away.
  const conversation = await page(
    "conversation",
    String.raw`<script lang="ts">
  import { tick } from "svelte";
  import ConversationPane from "../src/components/conversation/ConversationPane.svelte";
  import type { ConversationState } from "../src/lib/conversation/conversationState";

  const tool = (index: number) => ({
    key: "tool-" + index, kind: "tool_call", sequence: 100 + index, createdAt: 1000 + index,
    toolCallId: "tool-" + index, title: "Read file " + index, toolKind: "read",
    detail: "output line for tool " + index + "\n".repeat(12), startedDetail: null,
    cappedDetailSequence: null, status: "completed", progress: null
  });
  const working = [
    { key: "p1", kind: "prompt", sequence: 1, createdAt: 900,
      content: [{ piece: "text", text: "go" }], senderLabel: "owner", mode: "queue",
      sentAtUnixMilliseconds: 900 },
    ...[1, 2, 3, 4].map(tool)
  ];

  let rows = $state<any[]>(working);
  let running = $state(true);
  let conversationState = $state<ConversationState>("opened");
  let lens = $state<"focus" | "full">("focus");

  (window as any).__endTheTurn = () => {
    rows = [...working, { key: "end", kind: "turn_ended", sequence: 200, createdAt: 2000,
      ending: "completed", errorSummary: null, automaticCompactionResult: null }];
    running = false;
  };
  (window as any).__settle = async () => { await tick(); await tick(); };
</script>

<main style="height:720px">
  <ConversationPane
    bind:conversationState bind:lens conversationId="open-stays-open" label="Worker"
    conversationExists {rows} visibleRows={null} {running} heldPromptRows={[]}
    supportsSteer={false} backendKey="claude"
    current={{ model: "opus", reasoningEffort: "high" }}
    models={[{ model_id: "opus", display_name: "Opus", enabled: true }]}
    effortOptions={["high"]} outgoingMessages={[]} ownSenderLabel="owner"
    onSend={async () => true}
  />
</main>
<style>:global(html),:global(body),:global(#app){height:100%;margin:0}</style>`
  );

  // The Ticket screen. A stage the reader opened is rebuilt somewhere else on the page
  // when it settles. The new current step lives in its card, without opening Steps.
  const ticket = await page(
    "ticket",
    `<script lang="ts">
import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
import TicketRoute from '../src/routes/TicketRoute.svelte';
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
// What the change stream does when the server says something changed.
(window as any).__refresh = () => client.invalidateQueries();
</script>
<QueryClientProvider {client}><TicketRoute id="t_open" /></QueryClientProvider>`
  );

  // The Sprint documents. Which one is open is computed from which ones have text, so an
  // agent writing the Checkpoint recomputes it underneath the reader.
  const sprint = await page(
    "sprint",
    String.raw`<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { queryClient } from "../src/lib/queryClient";
  import SprintRoute from "../src/routes/SprintRoute.svelte";
  let sprint = {
    id: "sp_open", name: "A useful sprint", date_start: "2026-09-01", date_end: "2026-09-07",
    primary_bet: "Original summary", kickoff: "The plan we agreed", checkpoint: "", review: "",
    created_at: 1, updated_at: 1
  };
  const json = (value: unknown) =>
    new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/sprints/sp_open/tracking")
      return json({ sprint, planning_date: "2026-09-08", outcome_groups: [], unclassified_tickets: [] });
    if (path === "/api/sprints?detail=full") return json({ sprints: [sprint] });
    if (path.startsWith("/api/items?detail=summary"))
      return json({ items: [], page: { match_count: 0, return_count: 0, limit: 30, offset: 0,
        omitted_before: 0, omitted_after: 0, complete: true, next_offset: null } });
    return json({ projects: [], tickets: [] });
  }) as typeof fetch;
  (window as any).__writeTheCheckpoint = () => {
    sprint = { ...sprint, checkpoint: "Halfway, and here is where we are" };
    return queryClient.invalidateQueries();
  };
</script>
<QueryClientProvider client={queryClient}><SprintRoute sub="documents" sprintId="sp_open" /></QueryClientProvider>`
  );


  return {inputs: [join(root, conversation), join(root, ticket), join(root, sprint)], arguments: [join(root, conversation), join(root, ticket), join(root, sprint)], python: fileURLToPath(new URL("./open-stays-open.py", import.meta.url))};
}
