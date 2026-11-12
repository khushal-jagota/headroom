/** Thin seam adapter to the shared scripted process. Test hosts alone import this. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import * as backend from '../backends/contracts.ts';
import type {
  BackendChild,
  BackendEventSink,
  BackendEventValue,
  TurnToken,
} from '../backends/contracts.ts';
import type { AnswerFate, ResolvedConversationStart } from '../contracts.ts';
import type { MessageContent } from '../message_content.ts';
import type { UserInputAnswer } from '../events.ts';
import {
  decode_scripted_event,
  encode_scripted_event,
  scripted_content,
} from './scripted_protocol.ts';
import type { ScriptedBehavior, ScriptedFrame, ScriptedSnapshot } from './scripted_protocol.ts';

const error_classes = {
  BackendAdapterError: backend.BackendAdapterError,
  BackendSpawnFailed: backend.BackendSpawnFailed,
  SessionLoadFailed: backend.SessionLoadFailed,
  PromptWriteRefused: backend.PromptWriteRefused,
  PromptWriteFailed: backend.PromptWriteFailed,
  RemoteCommandUncertain: backend.RemoteCommandUncertain,
  DeliveryCancelled: backend.DeliveryCancelled,
  BackendPromptRejected: backend.BackendPromptRejected,
  PermissionAnswerWriteFailed: backend.PermissionAnswerWriteFailed,
  UserInputAnswerWriteFailed: backend.UserInputAnswerWriteFailed,
  NeedsRebind: backend.NeedsRebind,
};

export class ScriptedChild implements BackendChild {
  readonly stop_outside_lock = false;
  readonly #child = spawn(
    process.execPath,
    [fileURLToPath(new URL('./scripted_process.ts', import.meta.url))],
    { stdio: ['pipe', 'pipe', 'pipe'] },
  );
  readonly #pending = new Map<
    number,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >();
  readonly #event_reports = new Set<Promise<void>>();
  readonly #event_errors: unknown[] = [];
  readonly #exit: Promise<void>;
  #next_id = 0;
  #closed = false;
  last_observation: ScriptedSnapshot | null = null;
  #stderr = '';

  constructor(sink: BackendEventSink) {
    this.#child.stdin.on('error', (error) => this.#fail(error));
    this.#child.stderr.setEncoding('utf8');
    this.#child.stderr.on('data', (data: string) => {
      this.#stderr += data;
    });
    const lines = createInterface({ input: this.#child.stdout });
    lines.on('line', (line) => {
      try {
        const frame = JSON.parse(line) as ScriptedFrame;
        if ('event' in frame) {
          // Invoke immediately, but keep parsing replies while a report awaits a receipt.
          const task = Promise.resolve(sink.report(frame.turn, decode_scripted_event(frame.event)));
          this.#event_reports.add(task);
          void task.then(
            () => this.#event_reports.delete(task),
            (error: unknown) => {
              this.#event_reports.delete(task);
              this.#event_errors.push(error);
            },
          );
          return;
        }
        const pending = this.#pending.get(frame.id);
        if (!pending) {
          throw new Error(`unknown scripted reply ${frame.id}`);
        }
        this.#pending.delete(frame.id);
        if ('error' in frame) {
          const ErrorClass = error_classes[frame.error.name as keyof typeof error_classes];
          pending.reject(
            frame.error.name === 'NeedsRebind'
              ? new backend.NeedsRebind(frame.error.message, {
                  failed_child_recovery: frame.error.failed_child_recovery,
                })
              : new (ErrorClass ?? Error)(frame.error.message),
          );
        } else {
          pending.resolve(frame.result);
        }
      } catch (error) {
        this.#fail(error instanceof Error ? error : new Error(String(error)));
        this.#child.kill();
      }
    });
    this.#exit = new Promise<void>((resolve) => {
      this.#child.once('error', (error) => {
        this.#fail(error);
        resolve();
      });
      this.#child.once('exit', (code, signal) => {
        this.#fail(new Error(`scripted child exited (${signal ?? code}): ${this.#stderr}`));
        resolve();
      });
    });
  }

  #fail(error: Error): void {
    this.#closed = true;
    for (const pending of this.#pending.values()) {
      pending.reject(error);
    }
    this.#pending.clear();
  }

  #request<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
    if (this.#closed) {
      return Promise.reject(new Error('scripted child is closed'));
    }
    const id = ++this.#next_id;
    return new Promise<T>((resolve, reject) => {
      this.#pending.set(id, { resolve: (value) => resolve(value as T), reject });
      this.#child.stdin.write(JSON.stringify({ id, command, arguments: args }) + '\n', (error) => {
        if (error) {
          this.#pending.delete(id);
          reject(error);
        }
      });
    });
  }

  async start(
    resolved_start: ResolvedConversationStart,
    options: { vendor_session_cursor: string | null },
  ): Promise<void> {
    await this.#request('start', { resolved_start, ...options });
  }

  async write_prompt(
    turn_token: TurnToken,
    content: MessageContent,
    options: Parameters<BackendChild['write_prompt']>[2],
  ): Promise<backend.BackendPromptAccepted> {
    const { sender_content, ...rest } = options;
    return new backend.BackendPromptAccepted(
      await this.#request('write_prompt', {
        turn_token,
        content: scripted_content(content),
        sender_content: scripted_content(sender_content),
        ...rest,
      }),
    );
  }

  async steer(
    turn_token: TurnToken,
    content: MessageContent,
    options: Parameters<BackendChild['steer']>[2],
  ): Promise<backend.BackendSteerOutcome> {
    const result = await this.#request<{
      fate: 'accepted' | 'refused' | 'uncertain';
      composed_content_delivered: boolean | null;
      refusal_reason: ConstructorParameters<
        typeof backend.BackendSteerRefused
      >[0]['refusal_reason'];
    }>('steer', {
      turn_token,
      content: scripted_content(content),
      sender_content: options.sender_content ? scripted_content(options.sender_content) : null,
      sender_label: options.sender_label,
    });
    if (result.fate === 'refused') {
      return new backend.BackendSteerRefused(result);
    }
    if (result.fate === 'uncertain') {
      return new backend.BackendSteerUncertain(result);
    }
    return new backend.BackendSteerAccepted({
      composed_content_delivered: result.composed_content_delivered ?? true,
    });
  }

  async cancel_running_turn(): Promise<void> {
    await this.#request('cancel_running_turn');
  }

  async answer_permission_ask(
    ask_id: string,
    option_id: string,
    options: Parameters<BackendChild['answer_permission_ask']>[2] = {},
  ): Promise<AnswerFate> {
    return await this.#request('answer_permission_ask', { ask_id, option_id, ...options });
  }

  async answer_user_input(
    request_id: string,
    answers: readonly UserInputAnswer[],
    options: Parameters<BackendChild['answer_user_input']>[2] = {},
  ): Promise<AnswerFate> {
    return await this.#request('answer_user_input', { request_id, answers, ...options });
  }

  async withdraw_delivery(): Promise<boolean> {
    return await this.#request('withdraw_delivery');
  }

  async detach(): Promise<void> {
    await this.#request('detach');
    await this.inspect().catch(() => {});
    await this.#close();
  }

  async stop(): Promise<void> {
    if (this.#closed) {
      await this.#exit;
      return;
    }
    await this.#request('stop');
    await this.inspect().catch(() => {});
    await this.#close();
  }

  async #close(): Promise<void> {
    this.#child.stdin.end();
    await this.#exit;
  }

  async configure(command: string, behavior: ScriptedBehavior): Promise<void> {
    await this.#request('configure', { command, behavior });
  }

  async reject_next_prompt(): Promise<void> {
    await this.configure('write_prompt', {
      error: { name: 'BackendPromptRejected', message: 'Synthetic provider refused this prompt.' },
    });
  }

  async inspect(): Promise<ScriptedSnapshot> {
    const snapshot = await this.#request<ScriptedSnapshot>('inspect');
    this.last_observation = snapshot;
    return snapshot;
  }

  async wait_for_barrier(name: string): Promise<void> {
    await this.#request('wait_for_barrier', { name });
  }

  async release(name: string): Promise<void> {
    await this.#request('release', { name });
  }

  async emit(event: BackendEventValue, turn?: TurnToken | null): Promise<void> {
    await this.#request('emit', { event: encode_scripted_event(event), turn });
  }

  async request_questions(): Promise<void> {
    await this.#request('questions');
    await this.flush_events();
  }

  async finish(
    options: { ending?: 'completed' | 'failed'; error_summary?: string | null } = {},
  ): Promise<void> {
    await this.#request('finish', options);
    await this.flush_events();
  }

  async flush_events(): Promise<void> {
    while (this.#event_reports.size) {
      await Promise.allSettled([...this.#event_reports]);
    }
    if (this.#event_errors.length) {
      throw this.#event_errors.shift();
    }
  }

  /** Simulate process loss and clean up only this adapter's exact child process. */
  async terminate(): Promise<void> {
    this.#child.kill();
    await this.#exit;
  }
}
