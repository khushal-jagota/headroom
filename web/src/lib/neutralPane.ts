/**
 * neutralPane.ts — the framework-free neutral-envelope WS client for the Chief pane (S2b
 * plan §4). NO Svelte, NO DOM: a pure factory driven by injected deps, unit-tested in plain
 * Node exactly like `ws.ts` (see tests/neutral-pane.test.mjs). The Svelte wrapper
 * (`ChiefNeutralPane.svelte`) instantiates it with the real WebSocket/window deps and renders
 * the `onState` snapshot.
 *
 * Reactivity boundary: this is a LIVE WS client, NOT a keyed-invalidation resource. It runs a
 * SEPARATE socket to `/api/relay/neutral` with its own reconnect + re-attach lifecycle; it does
 * NOT register a resource, does NOT import the resource cache, and does NOT read Panels DB.
 * History comes only from the attach `history_snapshot` (the durable session).
 *
 * The wire shapes mirror `neutral_vocabulary.py` (the locked contract; that Python module is
 * the source of truth). Events are `{neutral:"event",kind,employee_entity_id,...}`; requests
 * are `{neutral:"request",kind,employee_entity_id,...}`.
 */

// --- wire kinds (mirror neutral_vocabulary.py — the Python module is the source of truth) ---

export const EVENT_KIND = {
  turnStarted: "turn_started",
  assistantTextDelta: "assistant_text_delta",
  thinkingDelta: "thinking_delta",
  toolActivity: "tool_activity",
  agentQuestion: "agent_question",
  toolApprovalRequest: "tool_approval_request",
  turnCompleted: "turn_completed",
  turnFailed: "turn_failed",
  sessionTitled: "session_titled",
  historySnapshot: "history_snapshot",
  childReset: "child_reset",
  catalogResult: "catalog_result",
  passthrough: "passthrough"
} as const;

export const REQUEST_KIND = {
  attachToEmployee: "attach_to_employee",
  sendMessage: "send_message",
  answerQuestion: "answer_question",
  respondToApproval: "respond_to_approval",
  interrupt: "interrupt",
  compact: "compact",
  listCatalog: "list_catalog",
  newConversation: "new_conversation"
} as const;

// --- rendered snapshot shape (what onState delivers to the Svelte view) ----------------------

export type TranscriptRole = "human" | "assistant" | "system" | "tool" | string;

export type TranscriptEntry = {
  role: TranscriptRole;
  text: string;
  toolName?: string | null;
  images?: readonly string[];
};

export type ToolActivity = {
  toolId: string;
  toolName: string;
  phase: string;
  preview: string;
};

export type PendingQuestion = {
  requestId: string;
  promptText: string;
  choices: readonly string[];
};

export type PendingApproval = {
  requestId: string;
  summary: string;
};

export type NeutralSnapshot = {
  transcript: readonly TranscriptEntry[];
  turnActive: boolean;
  streamingText: string;
  thinkingText: string;
  toolActivity: readonly ToolActivity[];
  pendingQuestion: PendingQuestion | null;
  pendingApproval: PendingApproval | null;
  title: string;
  catalogPayload: unknown | null;
  connected: boolean;
  // True once the FIRST history_snapshot has been applied — the readiness barrier (a socket
  // open alone is not ready; the pane must have its durable history before it is asserted on).
  historyLoaded: boolean;
};

// --- injected deps ---------------------------------------------------------------------------

export type NeutralSocket = {
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: (() => void) | null;
  onerror?: (() => void) | null;
};

export type NeutralPaneDeps = {
  url: string;
  employeeEntityId: string;
  socketFactory: (url: string) => NeutralSocket;
  now?: () => number;
  setTimeout: (callback: () => void, delayMs: number) => number;
  clearTimeout: (id: number) => void;
  onState: (snapshot: NeutralSnapshot) => void;
};

export type NeutralPaneClient = {
  attach(): void;
  send(text: string, imageRefs?: readonly string[]): void;
  answer(requestId: string, answer: string): void;
  respondApproval(requestId: string, decision: string, applyToAll: boolean): void;
  interrupt(): void;
  compact(): void;
  listCatalog(): void;
  newConversation(): void;
  dispose(): void;
  snapshot(): NeutralSnapshot;
};

const INITIAL_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;

type MutableState = {
  transcript: TranscriptEntry[];
  turnActive: boolean;
  streamingText: string;
  thinkingText: string;
  toolActivity: ToolActivity[];
  pendingQuestion: PendingQuestion | null;
  pendingApproval: PendingApproval | null;
  title: string;
  catalogPayload: unknown | null;
  connected: boolean;
  historyLoaded: boolean;
};

function freshState(): MutableState {
  return {
    transcript: [],
    turnActive: false,
    streamingText: "",
    thinkingText: "",
    toolActivity: [],
    pendingQuestion: null,
    pendingApproval: null,
    title: "",
    catalogPayload: null,
    connected: false,
    historyLoaded: false
  };
}

/** Clear all EPHEMERAL turn state (everything except the settled transcript + title +
 * catalog + connection). Used by history_snapshot (authoritative), turn completion/failure,
 * and child_reset. */
function clearEphemeral(state: MutableState): void {
  state.turnActive = false;
  state.streamingText = "";
  state.thinkingText = "";
  state.toolActivity = [];
  state.pendingQuestion = null;
  state.pendingApproval = null;
}

export function createNeutralPaneClient(deps: NeutralPaneDeps): NeutralPaneClient {
  const state = freshState();
  let socket: NeutralSocket | null = null;
  let reconnectTimer: number | null = null;
  let retryMs = INITIAL_RETRY_MS;
  let disposed = false;

  function toSnapshot(): NeutralSnapshot {
    return {
      transcript: state.transcript.slice(),
      turnActive: state.turnActive,
      streamingText: state.streamingText,
      thinkingText: state.thinkingText,
      toolActivity: state.toolActivity.slice(),
      pendingQuestion: state.pendingQuestion,
      pendingApproval: state.pendingApproval,
      title: state.title,
      catalogPayload: state.catalogPayload,
      connected: state.connected,
      historyLoaded: state.historyLoaded
    };
  }

  function emit(): void {
    deps.onState(toSnapshot());
  }

  function sendRequest(kind: string, extra: Record<string, unknown> = {}): void {
    if (socket === null) return;
    const wire = {
      neutral: "request",
      kind,
      employee_entity_id: deps.employeeEntityId,
      ...extra
    };
    socket.send(JSON.stringify(wire));
  }

  function openTurnIfNeeded(): void {
    if (!state.turnActive) {
      state.turnActive = true;
      state.streamingText = "";
    }
  }

  function closeTurn(finalText: string, failureLine: string | null): void {
    if (state.turnActive) {
      const text = failureLine !== null ? failureLine : finalText;
      if (text.length > 0) {
        state.transcript.push({ role: "assistant", text });
      }
    } else if (failureLine !== null && failureLine.length > 0) {
      // An IDLE failure (e.g. compact's 4009 with no running turn): append a standalone
      // failure line without corrupting state.
      state.transcript.push({ role: "system", text: failureLine });
    } else if (finalText.length > 0) {
      state.transcript.push({ role: "assistant", text: finalText });
    }
    clearEphemeral(state);
  }

  function upsertToolActivity(next: ToolActivity): void {
    const index = state.toolActivity.findIndex(
      (existing) => existing.toolId === next.toolId && existing.phase === next.phase
    );
    if (index >= 0) {
      state.toolActivity[index] = next;
    } else {
      state.toolActivity.push(next);
    }
  }

  function applyEvent(wire: Record<string, unknown>): void {
    const kind = String(wire.kind);
    // NO wrong-employee filter (F16b): the relay routes per-employee; a client-side id filter
    // would duplicate that invariant and could MASK a server routing defect.
    switch (kind) {
      case EVENT_KIND.turnStarted:
        openTurnIfNeeded();
        break;
      case EVENT_KIND.assistantTextDelta:
        openTurnIfNeeded();
        state.streamingText += String(wire.text ?? "");
        break;
      case EVENT_KIND.thinkingDelta:
        state.thinkingText += String(wire.text ?? "");
        break;
      case EVENT_KIND.toolActivity:
        upsertToolActivity({
          toolId: String(wire.tool_id ?? ""),
          toolName: String(wire.tool_name ?? ""),
          phase: String(wire.phase ?? ""),
          preview: String(wire.preview ?? "")
        });
        break;
      case EVENT_KIND.agentQuestion:
        state.pendingQuestion = {
          requestId: String(wire.request_id ?? ""),
          promptText: String(wire.prompt_text ?? ""),
          choices: Array.isArray(wire.choices) ? wire.choices.map(String) : []
        };
        break;
      case EVENT_KIND.toolApprovalRequest:
        state.pendingApproval = {
          requestId: String(wire.request_id ?? ""),
          summary: String(wire.summary ?? "")
        };
        break;
      case EVENT_KIND.turnCompleted:
        closeTurn(String(wire.final_text ?? ""), null);
        break;
      case EVENT_KIND.turnFailed:
        closeTurn("", failureText(wire));
        break;
      case EVENT_KIND.sessionTitled:
        state.title = String(wire.title ?? "");
        break;
      case EVENT_KIND.historySnapshot:
        state.transcript = historyMessages(wire);
        clearEphemeral(state);
        state.historyLoaded = true;
        break;
      case EVENT_KIND.childReset:
        clearEphemeral(state);
        // A respawn invalidates the live session server-side; re-attach re-syncs history.
        reattach();
        break;
      case EVENT_KIND.catalogResult:
        state.catalogPayload = parseCatalog(wire);
        break;
      case EVENT_KIND.passthrough:
        state.transcript.push({ role: "system", text: passthroughLine(wire) });
        break;
      default:
        // An unknown kind: leave state untouched (never crash the reducer).
        break;
    }
  }

  function onMessage(raw: string): void {
    let wire: Record<string, unknown>;
    try {
      wire = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    if (wire === null || typeof wire !== "object" || wire.neutral !== "event") return;
    applyEvent(wire);
    emit();
  }

  function bindSocket(active: NeutralSocket): void {
    active.onopen = () => {
      if (disposed || socket !== active) return;
      retryMs = INITIAL_RETRY_MS;
      state.connected = true;
      // The attach's history snapshot is the recovery mechanism (re-sync on every open).
      sendRequest(REQUEST_KIND.attachToEmployee);
      emit();
    };
    active.onmessage = (event) => {
      if (disposed || socket !== active) return;
      onMessage(event.data);
    };
    active.onclose = () => {
      if (disposed || socket !== active) return;
      socket = null;
      state.connected = false;
      emit();
      scheduleReconnect();
    };
    if (active.onerror !== undefined) {
      active.onerror = () => {
        if (disposed || socket !== active) return;
        active.close();
      };
    }
  }

  function connect(): void {
    if (disposed) return;
    const active = deps.socketFactory(deps.url);
    socket = active;
    bindSocket(active);
  }

  function scheduleReconnect(): void {
    if (disposed || reconnectTimer !== null) return;
    reconnectTimer = deps.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, retryMs);
    retryMs = Math.min(retryMs * 2, MAX_RETRY_MS);
  }

  function reattach(): void {
    // Re-send attach on the CURRENT socket (child reset recovery: same socket, fresh history).
    sendRequest(REQUEST_KIND.attachToEmployee);
  }

  return {
    attach(): void {
      if (socket === null) connect();
    },
    send(text: string, imageRefs: readonly string[] = []): void {
      // Optimistic human line (F8): S2a emits NO user-message event, so append immediately;
      // the next authoritative history_snapshot replaces the whole settled transcript.
      state.transcript.push({
        role: "human",
        text,
        images: imageRefs.length ? imageRefs.slice() : undefined
      });
      emit();
      sendRequest(REQUEST_KIND.sendMessage, { text, image_refs: imageRefs.slice() });
    },
    answer(requestId: string, answer: string): void {
      state.pendingQuestion = null;
      emit();
      sendRequest(REQUEST_KIND.answerQuestion, { request_id: requestId, answer });
    },
    respondApproval(requestId: string, decision: string, applyToAll: boolean): void {
      state.pendingApproval = null;
      emit();
      sendRequest(REQUEST_KIND.respondToApproval, {
        request_id: requestId,
        decision,
        apply_to_all: applyToAll
      });
    },
    interrupt(): void {
      sendRequest(REQUEST_KIND.interrupt);
    },
    compact(): void {
      sendRequest(REQUEST_KIND.compact);
    },
    listCatalog(): void {
      sendRequest(REQUEST_KIND.listCatalog);
    },
    newConversation(): void {
      // Clear ALL local state on send (in-flight text, thinking, tools, pending question,
      // pending approval, AND the settled transcript incl. any optimistic human line): the
      // incoming EMPTY HistorySnapshotEvent is authoritative and rebuilds from scratch
      // (plan §3.3/§4.2). Emit so the pane blanks immediately, then request the rebind.
      state.transcript = [];
      clearEphemeral(state);
      state.title = "";
      emit();
      sendRequest(REQUEST_KIND.newConversation);
    },
    dispose(): void {
      disposed = true;
      if (reconnectTimer !== null) {
        deps.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (socket !== null) {
        socket.close();
        socket = null;
      }
    },
    snapshot(): NeutralSnapshot {
      return toSnapshot();
    }
  };
}

// --- wire field helpers ----------------------------------------------------------------------

function historyMessages(wire: Record<string, unknown>): TranscriptEntry[] {
  const rows = Array.isArray(wire.messages) ? wire.messages : [];
  return rows.map((row) => {
    const message = row as Record<string, unknown>;
    return {
      role: String(message.role ?? ""),
      text: String(message.text ?? ""),
      toolName: message.tool_name == null ? null : String(message.tool_name)
    };
  });
}

function failureText(wire: Record<string, unknown>): string {
  const reason = String(wire.reason ?? "");
  const detail = String(wire.detail ?? "");
  if (reason === "interrupted") return detail || "(interrupted)";
  if (detail) return detail;
  return reason || "(turn failed)";
}

function passthroughLine(wire: Record<string, unknown>): string {
  const nativeType = String(wire.native_type ?? "");
  return nativeType ? `[${nativeType}]` : "[system]";
}

function parseCatalog(wire: Record<string, unknown>): unknown {
  const payloadJson = wire.payload_json;
  if (typeof payloadJson !== "string") return null;
  try {
    return JSON.parse(payloadJson);
  } catch {
    return null;
  }
}
