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
  import { provideNavigationApplication, navigationApplication } from "../src/lib/navigation/application.svelte";

  const navigation = provideNavigationApplication();
  (window as any).__navigation = navigation;
  (window as any).__sameNavigation = navigationApplication() === navigation;
  (window as any).__beforeRecover = [];
  const recover = navigation.owner.recover;
  navigation.owner.recover = () => {
    (window as any).__beforeRecover.push(Object.fromEntries(['conversation-a','conversation-b'].map(id =>
      [id,navigation.state.effective?.conversations.get(id)?.message_for_owner])));
    return recover();
  };

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
    String.raw`import { SCHEMA_VERSION, SUPPORTED_SEMANTICS } from "../src/lib/navigation/contracts";
import { encodeCursor } from "../src/lib/navigation/validation";
import { authority, conversation } from "../tests/support/navigation/fixtures";
(globalThis as any).__navigationSnapshots = 0;
(globalThis as any).__navigationStreams = 0;
(globalThis as any).__closedNavigationStreams = 0;
const snapshots: Record<string, any> = Object.fromEntries(
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
const navigationBoundaries: Record<string,number> = {'conversation-a':0,'conversation-b':0};
(globalThis as any).__navigationBoundaries=navigationBoundaries;
let holdNavigation = false;
let releaseNavigation: (()=>void) | undefined;
(globalThis as any).__holdNextNavigation = () => {holdNavigation=true;};
(globalThis as any).__releaseNavigation = () => releaseNavigation?.();

(globalThis as any).__delayNextASnapshot = () => (delayNextASnapshot = true);
(globalThis as any).__delayedASnapshotRequested = () => delayedASnapshotRequested;
(globalThis as any).__releaseDelayedASnapshot = () => releaseDelayedASnapshot?.();
(globalThis as any).fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/navigation/snapshot") {
    (globalThis as any).__navigationSnapshots += 1;
    if (holdNavigation) {
      holdNavigation=false;
      await new Promise<void>(resolve=>releaseNavigation=resolve);
      releaseNavigation=undefined;
    }
    const continuity = {scope: "workspace", schema_version: SCHEMA_VERSION,
      semantics: SUPPORTED_SEMANTICS, epoch: "read-fixture", revision: String((globalThis as any).__navigationSnapshots)};
    return Response.json({wire_version: 2, mode: 'current', authority: authority(), cursor: encodeCursor(continuity),
      snapshot: {...continuity, records: [conversation("conversation-a",navigationBoundaries["conversation-a"]),conversation("conversation-b",navigationBoundaries["conversation-b"])]}, proof: null});
  }
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

  navigation: boolean;
  constructor(url: string) {
    this.navigation = url.startsWith("/api/navigation/events?");
    if (this.navigation) (globalThis as any).__navigationStreams += 1;
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
  close(): void {
    if (this.navigation) (globalThis as any).__closedNavigationStreams += 1;
  }
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
  const { mount, unmount } = await import("svelte");
  const { default: Host } = await import("./${hostPath.split("/").at(-1)}");
  const host = mount(Host, { target: document.getElementById("app")! });
  (globalThis as any).__unmountHost = () => unmount(host);
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
