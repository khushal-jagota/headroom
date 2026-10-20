// Generated from planner.conversation.history_contracts. Do not edit.
export type HistoryOwnerKind = "ticket" | "sprint_item";
export interface ConversationHistoryEntry { conversation_id: string; created_at: number; label: string; }
export interface ConversationHistoryPage { revision: string; offset: number; total: number; active_index: number | null; entries: Array<ConversationHistoryEntry>; }
