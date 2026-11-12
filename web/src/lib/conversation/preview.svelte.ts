import { createQuery } from "@tanstack/svelte-query";
import { untrack } from "svelte";
import { jsonQuery, queries } from "../queryCatalogue";
import { macActivityKnown, type TicketLocale } from "../ticketMachines";
import { ConversationViewReader } from "./view/reader.svelte";
import type { ConversationView } from "./wire";
import { resolveModelPicker } from "./modelPicker";
import { backgroundTasksAfterTheTurn, backgroundWorkSentence } from "./transcript";
import { preselectedValue } from "./composer";

export type ConversationPreviewSource = {
  visible: boolean;
  target: { kind: "chief" | "manager" | "ticket"; id: string } | null;
  conversationId: string | null;
  locale?: TicketLocale;
};

/** A visible preview reads the same compact live view as the conversation dock.
 * It never fetches transcript pages or marks a conversation as read. */
export function conversationPreview(source: () => ConversationPreviewSource) {
  let reader = $state.raw<ConversationViewReader | null>(null);
  const record = createQuery(() => {
    const current = source();
    return {
      ...jsonQuery<ConversationView>(["conversation-preview", current.conversationId],
        `/api/conversation/conversations/${encodeURIComponent(current.conversationId ?? "")}`),
      enabled: current.visible && current.conversationId !== null,
      retry: false
    };
  });
  const start = createQuery(() => {
    const current = source();
    const target = current.target;
    const query = target?.kind === "ticket" ? queries.ticketConversationStartValues(target.id)
      : target?.kind === "manager" ? queries.managerConversationStartValues(target.id)
      : queries.chiefConversationStartValues();
    return { ...query, enabled: current.visible && target !== null && (current.conversationId === null
      || Boolean(record.data && (record.data.model === null || record.data.reasoning_effort === null))), retry: false };
  });
  const backends = createQuery(() => {
    const current = source();
    return {
      ...(current.target?.kind === "ticket" ? queries.ticketBackends(current.target.id) : queries.backends()),
      enabled: current.visible && current.target !== null
    };
  });
  const health = createQuery(() => ({ ...queries.executorHealth(), enabled: source().visible && source().locale === "macos" }));

  const activeConversationId = $derived.by(() => {
    const current = source();
    return current.visible ? current.conversationId : null;
  });
  $effect(() => {
    const id = activeConversationId;
    if (id === null) {
      reader = null;
      return;
    }
    const next = new ConversationViewReader(id, "full", false);
    reader = next;
    void next.connect().catch(() => undefined);
    return () => { next.close(); };
  });
  const turnKey = $derived(reader?.status?.turn_key);
  $effect(() => {
    // Model changes apply when a new turn starts. Refresh once at that boundary,
    // rather than reading the record again for every tool update in its live tail.
    const turn = turnKey;
    if (turn !== undefined && turn !== null) untrack(() => { void record.refetch(); });
  });

  const status = $derived(reader?.status ?? null);
  const unknownMac = $derived(source().locale === "macos" && (health.isError || !macActivityKnown(health.data)));
  const running = $derived(!unknownMac && status?.authority === "current" && status.turn_running === true);
  const modelLabel = $derived.by(() => {
    const values = source().conversationId === null ? start.data : record.data;
    if (!values) return "";
    return resolveModelPicker({ backendKey: values.backend_key,
      model: preselectedValue(values.model, start.data?.model),
      reasoningEffort: preselectedValue(values.reasoning_effort, start.data?.reasoning_effort),
      backends: backends.data ?? [] }).face;
  });
  const activity = $derived.by(() => {
    if (unknownMac) return "Activity unknown on Mac";
    if (source().conversationId === null) return "Idle";
    if (!status) return reader?.trouble ? "Activity unknown" : null;
    if (status.authority !== "current") return "Activity unknown";
    if (status.pending_permission || status.pending_user_input) return "Waiting for you";
    if (running) return "Working";
    const background = backgroundTasksAfterTheTurn(status);
    if (background.length > 0) return backgroundWorkSentence(background.map(task => task.task_description));
    return "Idle";
  });
  return {
    get modelLabel() { return modelLabel; },
    get status() { return status; },
    get reader() { return reader; },
    get activity() { return activity; },
    get running() { return running; },
    get startedAtUnixMilliseconds() { return running ? status?.rest.latest_turn?.started_at_unix_milliseconds ?? null : null; }
  };
}
