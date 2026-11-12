/** Admission and settlement are native facts, never input queue offers. */
import {
  CLAUDE_STEER_ADMISSION_TIMEOUT_SECONDS,
  CLAUDE_STEER_SETTLEMENT_TIMEOUT_SECONDS,
} from './configuration.ts';
import { Pending, bounded, object, raw_user_message_uuids } from './wire.ts';

interface ObservedUserMessage {
  admission: Pending<boolean | null>;
  settled: Pending<void>;
  started: boolean;
  admission_consumed: boolean;
  conflicting_admission: boolean;
}
const rejected = new Set(['cancelled', 'discarded', 'refused']);
const terminal = new Set(['completed', ...rejected]);

export class ClaudeReceipts {
  private readonly user_messages = new Map<string, ObservedUserMessage>();
  private readonly results = new Map<string, Set<string>>();
  private readonly controls = new Map<string, Pending<Record<string, unknown> | null>>();

  watch_user_message(uuid: string): void {
    this.user_messages.set(uuid, {
      admission: new Pending(),
      settled: new Pending(),
      started: false,
      admission_consumed: false,
      conflicting_admission: false,
    });
  }
  forget_user_message(uuid: string): void {
    const observed = this.user_messages.get(uuid);
    this.user_messages.delete(uuid);
    if (!observed) return;
    observed.admission.resolve(false);
    observed.settled.resolve();
  }
  private accept(observed: ObservedUserMessage): void {
    if (!observed.admission.settled) observed.admission.resolve(true);
    else if (observed.admission.value === false) observed.conflicting_admission = true;
  }
  private retire(): void {
    for (const [uuid, observed] of this.user_messages) {
      if (observed.admission_consumed && observed.settled.settled) this.user_messages.delete(uuid);
    }
  }
  observe(message: Record<string, unknown>): void {
    if (message.type === 'command_lifecycle') {
      const observed =
        typeof message.command_uuid === 'string'
          ? this.user_messages.get(message.command_uuid)
          : undefined;
      const state = message.state;
      if (observed !== undefined && typeof state === 'string') {
        if (state === 'started') observed.started = true;
        if (state === 'queued' || state === 'started' || state === 'completed')
          this.accept(observed);
        else if (rejected.has(state)) observed.admission.resolve(false);
        if (terminal.has(state)) observed.settled.resolve();
      }
    }
    if (message.type === 'result' && typeof message.uuid === 'string') {
      const uuids = raw_user_message_uuids(message);
      this.results.set(message.uuid, uuids);
      for (const uuid of uuids) {
        const observed = this.user_messages.get(uuid);
        if (observed) {
          this.accept(observed);
          observed.settled.resolve();
        }
      }
    }
    this.retire();
    if (message.type === 'control_response' && object(message.response)) {
      const response = message.response;
      const pending =
        typeof response.request_id === 'string'
          ? this.controls.get(response.request_id)
          : undefined;
      pending?.resolve(object(response.response) ? response.response : null);
    }
  }
  async wait_for_user_message_admission(uuid: string): Promise<boolean | null> {
    const observed = this.user_messages.get(uuid);
    if (!observed) return null;
    try {
      const result = await bounded(
        observed.admission.promise,
        CLAUDE_STEER_ADMISSION_TIMEOUT_SECONDS,
      );
      return result.timed_out || observed.conflicting_admission ? null : result.value;
    } finally {
      observed.admission_consumed = true;
      this.retire();
    }
  }
  user_message_uuids_for_result(uuid: string | null): ReadonlySet<string> {
    if (uuid === null) return new Set();
    const values = this.results.get(uuid) ?? new Set<string>();
    this.results.delete(uuid);
    return values;
  }
  user_message_is_settled(uuid: string): boolean {
    const observed = this.user_messages.get(uuid);
    return observed === undefined || observed.settled.settled;
  }
  async wait_for_user_message_settlement(uuid: string): Promise<void> {
    const observed = this.user_messages.get(uuid);
    if (!observed) return;
    if (!observed.admission.settled) {
      const admission = await bounded(
        observed.admission.promise,
        CLAUDE_STEER_ADMISSION_TIMEOUT_SECONDS,
      );
      if (admission.timed_out) {
        observed.settled.resolve();
        return;
      }
    }
    const drained_before_wait = observed.started;
    const settled = await bounded(
      observed.settled.promise,
      CLAUDE_STEER_SETTLEMENT_TIMEOUT_SECONDS,
    );
    if (!settled.timed_out) return;
    if (drained_before_wait || !observed.started) {
      observed.settled.resolve();
      return;
    }
    await observed.settled.promise;
  }
  watch_control(request_id: string): Promise<Record<string, unknown> | null> {
    const pending = new Pending<Record<string, unknown> | null>();
    this.controls.set(request_id, pending);
    return pending.promise;
  }
  forget_control(request_id: string): void {
    this.controls.delete(request_id);
  }
  stream_ended(): void {
    for (const observed of this.user_messages.values()) {
      observed.admission.resolve(null);
      observed.settled.resolve();
    }
    this.retire();
    for (const pending of this.controls.values()) pending.resolve(null);
  }
  clear(): void {
    this.stream_ended();
    this.user_messages.clear();
    this.results.clear();
    this.controls.clear();
  }
}
