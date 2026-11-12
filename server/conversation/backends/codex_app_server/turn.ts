/** Native turn identities and provisional activity before correlated admission. */
import type { TurnToken } from '../contracts.ts';
import type { CodexLoginGeneration } from '../../snapshot/provider_contracts.ts';
import type * as P from './protocol.ts';
export class Deferred<T> {
  readonly promise: Promise<T>;
  done = false;
  value: T | undefined;
  private resolve_value!: (value: T) => void;
  constructor() { this.promise = new Promise<T>((resolve) => { this.resolve_value = resolve; }); }
  resolve(value: T): void { if (!this.done) { this.done = true; this.value = value; this.resolve_value(value); } }
}
export class TurnInFlight {
  readonly token: TurnToken;
  readonly started = new Deferred<string>();
  readonly ended = new Deferred<void>();
  kind: 'ordinary' | 'review' | 'compact' | 'goal' = 'ordinary';
  turn_id: string | null = null;
  interrupt_turn_id: string | null = null;
  readonly agent_message_texts = new Map<string, string[]>();
  readonly parked_asks = new Map<string, { request_id: unknown; method: string }>();
  readonly parked_user_inputs = new Map<string, { request_id: unknown }>();
  last_error_summary: string | null = null;
  standard_error_from_byte: number;
  login_generation_at_start: CodexLoginGeneration | null;
  pending_notifications: [string, P.ConsumedNotification][] = [];
  pending_requests: [string, unknown, P.HandledServerRequest][] = [];
  awaiting_start_response = false;
  constructor(token: TurnToken, standard_error_from_byte: number, generation: CodexLoginGeneration | null) {
    this.token = token;
    this.standard_error_from_byte = standard_error_from_byte;
    this.login_generation_at_start = generation;
  }
}
export function same_turn(one: TurnToken, other: TurnToken): boolean {
  return one.conversation_id === other.conversation_id && one.turn_number === other.turn_number;
}
export async function bounded<T>(task: Promise<T>, seconds: number): Promise<{ completed: true; value: T } | { completed: false }> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      task.then((value) => ({ completed: true as const, value })),
      new Promise<{ completed: false }>((resolve) => { timer = setTimeout(() => resolve({ completed: false }), seconds * 1000); }),
    ]);
  } finally { clearTimeout(timer); }
}
