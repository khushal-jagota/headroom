import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `live-switch-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `live-switch-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `live-switch-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import LiveConversation from "../src/components/conversation/LiveConversation.svelte";

  let conversationId = $state("conversation-a");
  let conversationState = $state<"rest" | "peeked" | "opened">("opened");
  (window as any).__setConversationState = (state: typeof conversationState) => conversationState = state;
  (window as any).__switchConversation = (id: string) => (conversationId = id);

  async function sendMessage(): Promise<any> {
    return { conversation_id: conversationId, fate: "started" };
  }

  async function newConversation(): Promise<void> {
    conversationId = null;
  }
</script>

<LiveConversation
  {conversationId}
  persistenceKey="switch-fixture"
  label="Worker"
  bind:conversationState
  {sendMessage}
  onNewConversation={newConversation}
/>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    String.raw`const snapshots: Record<string, any> = Object.fromEntries(
  ["conversation-a", "conversation-b"].map((conversationId) => [conversationId, {
    conversation_id: conversationId,
    backend_key: "codex",
    supports_steer: true,
    model: "gpt-test",
    reasoning_effort: null,
    workspace_folder: "/tmp",
    access: "full",
    role_text: null,
    identity_environment_variable_names: [],
    latest_sequence: 4,
    owner_read_through_sequence: 0,
    is_running: false,
    held_prompts: [],
    pending_permission_ask: null,
    pending_user_input: null,
    composer_catalog: []
  }])
);

(globalThis as any).__ownerReads = [];
let focused = true;
let visible = true;
Object.defineProperty(document, "hasFocus", { value: () => focused });
Object.defineProperty(document, "visibilityState", { get: () => visible ? "visible" : "hidden" });
(globalThis as any).__setAttention = (nextFocused: boolean, nextVisible: boolean) => {
  focused = nextFocused;
  visible = nextVisible;
  document.dispatchEvent(new Event("visibilitychange"));
  window.dispatchEvent(new Event(focused ? "focus" : "blur"));
};
const heldReads = new Map<number, { resolve: (accepted: number) => void; reject: () => void }>();
const holdSequences = new Set<number>();
(globalThis as any).__holdRead = (sequence: number) => holdSequences.add(sequence);
(globalThis as any).__finishRead = (sequence: number, succeeded = true) => {
  const pending = heldReads.get(sequence)!;
  if (succeeded) pending.resolve(sequence);
  else pending.reject();
  heldReads.delete(sequence);
};
let delayNextRead = false;
let releaseRead: (() => void) | null = null;
(globalThis as any).__delayNextRead = () => delayNextRead = true;
(globalThis as any).__releaseRead = () => releaseRead?.();
let delayNextASnapshot = false;
let delayedASnapshotRequested = false;
let releaseDelayedASnapshot: (() => void) | null = null;
const recordedBRows: unknown[] = [];
(globalThis as any).__delayNextASnapshot = () => (delayNextASnapshot = true);
(globalThis as any).__delayedASnapshotRequested = () => delayedASnapshotRequested;
(globalThis as any).__releaseDelayedASnapshot = () => releaseDelayedASnapshot?.();
(globalThis as any).fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const ownerReadMatch = url.match(/conversations\/(conversation-[ab])\/owner-read$/);
  if (ownerReadMatch) {
    const body = JSON.parse(String(init?.body));
    (globalThis as any).__ownerReads.push({
      conversationId: ownerReadMatch[1],
      sequence: body.through_sequence
    });
    if (delayNextRead) {
      delayNextRead = false;
      await new Promise<void>((resolve) => releaseRead = resolve);
    }
    if (holdSequences.delete(body.through_sequence)) {
      try {
        await new Promise<number>((resolve, reject) => heldReads.set(body.through_sequence,
          { resolve, reject: () => reject(new Error("read refused")) }));
      } catch {
        return Response.json({ detail: "read refused" }, { status: 503 });
      }
    }
    const snapshot = snapshots[ownerReadMatch[1]];
    snapshot.owner_read_through_sequence = Math.max(snapshot.owner_read_through_sequence, body.through_sequence);
    return Response.json({ owner_read_through_sequence: body.through_sequence });
  }
  const eventsMatch = url.match(/conversations\/(conversation-[ab])\/events\?after=\d+$/);
  if (eventsMatch) {
    return Response.json({
      events: eventsMatch[1] === "conversation-b"
        ? recordedBRows
        : []
    });
  }
  const snapshotMatch = url.match(/conversations\/(conversation-[ab])$/);
  if (snapshotMatch) {
    if (snapshotMatch[1] === "conversation-a" && delayNextASnapshot) {
      delayNextASnapshot = false;
      delayedASnapshotRequested = true;
      await new Promise<void>((resolve) => (releaseDelayedASnapshot = resolve));
    }
    return Response.json(snapshots[snapshotMatch[1]]);
  }
  throw new Error("unexpected fetch " + url);
};

class FakeEventSource {
  static sources = new Map<string, FakeEventSource>();
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(url: string) {
    const conversationId = url.match(/conversations\/(conversation-[ab])\/tail/)?.[1];
    if (conversationId) FakeEventSource.sources.set(conversationId, this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, data: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(data) }));
    }
  }

  // Deliberately keep queued callbacks callable after close. This models a browser event
  // that was already dispatched when the pane switched conversations.
  close(): void {}
}

(globalThis as any).EventSource = FakeEventSource;
(globalThis as any).__tailExists = (id: string) => FakeEventSource.sources.has(id);
(globalThis as any).__emitConversationRow = (id: string, row: unknown) => {
  if (id === "conversation-b") recordedBRows.push(row);
  FakeEventSource.sources.get(id)?.emit("conversation-event", row);
};

async function start(): Promise<void> {
  const { queryClient } = await import("../src/lib/queryClient");
  (globalThis as any).__readRefreshes = 0;
  const invalidate = queryClient.invalidateQueries.bind(queryClient);
  queryClient.invalidateQueries = (...args) => {
    (globalThis as any).__readRefreshes += 1;
    return invalidate(...args);
  };
  const { mount } = await import("svelte");
  const { default: Host } = await import("./${hostPath.split("/").at(-1)}");
  mount(Host, { target: document.getElementById("app")! });
}
void start();
`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );


  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./live-conversation-switch.py", import.meta.url))};
}
