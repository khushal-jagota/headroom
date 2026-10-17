import { render } from "svelte/server";
import ConversationSignalMark from "../../src/components/ConversationSignalMark.svelte";
import type { ConversationSignal } from "../../src/lib/conversationSignal";

export function conversationSignalReading<T extends object>(facts: T & { conversation_signal?: ConversationSignal }): string {
  return render(ConversationSignalMark, { props: { signal: facts.conversation_signal, conversationId: "exact" } }).body;
}
