/** Durable knowledge, never a restored process or callback. */
import type { TransactionSql } from '../../core/database/contracts.ts';

export const LIFECYCLE_CONTRACT_VERSION = 1;
export const LifecycleKind = { turn: 'turn', attempt: 'attempt', request: 'request' } as const;
export type LifecycleKind = typeof LifecycleKind[keyof typeof LifecycleKind];
export const LifecycleOperation = {
    turn: 'turn', prompt: 'prompt', steer: 'steer', permission: 'permission',
    user_input: 'user_input', permission_answer: 'permission_answer',
    user_input_answer: 'user_input_answer', cancel: 'cancel',
} as const;
export type LifecycleOperation = typeof LifecycleOperation[keyof typeof LifecycleOperation];
export const LifecycleStatus = {
    intent: 'intent', running: 'running', pending: 'pending', answering: 'answering',
    started: 'started', injected: 'injected', accepted: 'accepted', refused: 'refused',
    suppressed: 'suppressed', unknown: 'unknown', completed: 'completed', failed: 'failed',
    interrupted: 'interrupted', unavailable: 'unavailable', answered: 'answered',
} as const;
export type LifecycleStatus = typeof LifecycleStatus[keyof typeof LifecycleStatus];

export interface LifecycleFact {
    readonly kind: LifecycleKind;
    readonly identity: string;
    readonly turn_number: number;
    readonly operation: LifecycleOperation;
    readonly status: LifecycleStatus;
    readonly provider_identity: string | null;
}
export interface LifecycleTransition {
    readonly conversation_id: string;
    readonly facts: readonly LifecycleFact[];
    readonly receipt_sequences: readonly number[];
    readonly contract_version: typeof LIFECYCLE_CONTRACT_VERSION;
}
/** Shares the caller's transaction. Never commits, awaits, or calls providers. */
export type ConversationTransactionParticipant = (tx: TransactionSql, candidate: LifecycleTransition) => void;
