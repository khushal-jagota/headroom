/** VPS backend adapter. The Mac retains native process ownership; calls create mailbox rows. */
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { isAbsolute, relative, sep } from 'node:path';
import * as backend from '../conversation/backends/contracts.ts';
import type {
  BackendChild,
  BackendEventSink,
  ChildReference,
  TurnToken,
} from '../conversation/backends/contracts.ts';
import {
  AnswerFate,
  ConversationBackendKey,
  PromptDeliveryRefusalReason,
  WorkspacePathPolicy,
} from '../conversation/contracts.ts';
import type { ResolvedConversationStart } from '../conversation/contracts.ts';
import type { UserInputAnswer } from '../conversation/events.ts';
import type { MessageContent } from '../conversation/message_content.ts';
import { resolve_path_sync } from '../core/path_resolution.ts';
import { pure_posix_path } from '../core/python_path.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { python_truth } from '../conversation/backends/claude_agent_sdk/events.ts';
import { now_ms } from './clock.ts';
import { ProtocolRefused, KeyError, ValueError } from './protocol.ts';
import { inspect } from 'node:util';
import { validate_skill_snapshot } from './skills.ts';
import type { WireTypes, ChildCommandArguments, ChildOperation } from './wire.generated.ts';

export type SkillSnapshotSupplier = (
  start: ResolvedConversationStart,
) => WireTypes['CanonicalSkillSnapshot'];
/** A fetched steer with no timely outcome. True later proves that it never reached its turn. */
export class LateSteer {
  readonly not_delivered: Promise<boolean>;
  constructor(not_delivered: Promise<boolean>) {
    this.not_delivered = not_delivered;
  }
}
export interface RemoteChildCommands {
  put(command: WireTypes['ChildCommand']): Promise<boolean>;
  mac_is_present(): boolean;
  deliver_steer(
    command: WireTypes['ChildCommand'],
  ): Promise<WireTypes['CommandOutcome'] | LateSteer | null>;
  stop(command: WireTypes['ChildCommand']): Promise<WireTypes['CommandOutcome']>;
  save_answer(command: WireTypes['ChildCommand']): Promise<boolean>;
  cancel_initial_delivery(child: ChildReference, command_ids: readonly string[]): Promise<boolean>;
  retire_child(child: ChildReference): void;
}
function raise_for(outcome: WireTypes['CommandOutcome']): void {
  if (outcome.disposition === 'uncertain')
    throw new backend.RemoteCommandUncertain(outcome.command_id);
  if (outcome.disposition !== 'refused') return;
  const result = outcome.result;
  const error_name = String(
    Object.hasOwn(result, 'error_type') ? result.error_type : 'BackendSpawnFailed',
  );
  if (error_name === 'BackendPromptRejected')
    throw new backend.BackendPromptRejected(
      String(
        Object.hasOwn(result, 'error_message') ? result.error_message : 'provider rejected prompt',
      ),
    );
  const errors: Record<string, new (message: string) => backend.BackendAdapterError> = {
    BackendSpawnFailed: backend.BackendSpawnFailed,
    SessionLoadFailed: backend.SessionLoadFailed,
    PromptWriteFailed: backend.PromptWriteFailed,
    PromptWriteRefused: backend.PromptWriteRefused,
    PermissionAnswerWriteFailed: backend.PermissionAnswerWriteFailed,
    UserInputAnswerWriteFailed: backend.UserInputAnswerWriteFailed,
    NeedsRebind: backend.NeedsRebind,
  };
  const ErrorType = Object.hasOwn(errors, error_name)
    ? errors[error_name]!
    : backend.BackendSpawnFailed;
  throw new ErrorType(
    String(
      Object.hasOwn(result, 'error_message') ? result.error_message : 'remote operation refused',
    ),
  );
}
const REPLACED_MESSAGE = 'The Mac executor process that owned this child was replaced';
export class MacRemoteChild implements BackendChild {
  readonly reference: ChildReference;
  readonly event_sink: BackendEventSink | null;
  readonly initial_history_complete: boolean;
  readonly stop_outside_lock = true;
  initial_delivery_cancelled = false;
  private readonly commands: RemoteChildCommands;
  private readonly skill_snapshot_supplier: SkillSnapshotSupplier;
  private delivery_commands: WireTypes['ChildCommand'][] = [];
  private readonly initial_puts = new Set<Promise<boolean>>();
  private claude_started_on: readonly [string, string | null] | null = null;
  private stop_task: Promise<void> | null = null;
  private stop_settlement: Promise<void> = Promise.resolve();
  constructor(
    reference: ChildReference,
    commands: RemoteChildCommands,
    skill_snapshot_supplier: SkillSnapshotSupplier,
    options: {
      event_sink?: BackendEventSink | null;
      initial_history_complete?: boolean;
      started_on?: ResolvedConversationStart | null;
    } = {},
  ) {
    this.reference = reference;
    this.commands = commands;
    this.skill_snapshot_supplier = skill_snapshot_supplier;
    this.event_sink = options.event_sink ?? null;
    this.initial_history_complete = options.initial_history_complete ?? true;
    if (options.started_on != null) this.note_start(options.started_on);
  }
  private note_start(start: ResolvedConversationStart): void {
    if (start.backend_key === ConversationBackendKey.claude)
      this.claude_started_on = [start.model, start.reasoning_effort];
  }
  async detach(options?: StopOptions): Promise<void> {
    this.commands.retire_child(this.reference);
    if (this.stop_task !== null) await this.observe_stop(this.stop_task, options);
  }
  async withdraw_delivery(): Promise<boolean> {
    this.initial_delivery_cancelled = true;
    await Promise.allSettled([...this.initial_puts]);
    const may_have_sent = await this.commands.cancel_initial_delivery(
      this.reference,
      this.delivery_commands.map((command) => command.command_id),
    );
    return this.initial_history_complete && !may_have_sent;
  }
  private command(
    arguments_: ChildCommandArguments & { kind: ChildOperation },
    turn: TurnToken | null = null,
  ): WireTypes['ChildCommand'] {
    return {
      command_id: randomUUID(),
      child: this.reference,
      operation: arguments_.kind,
      turn,
      arguments: arguments_,
      file_dependencies: [],
    };
  }
  private async put_initial(command: WireTypes['ChildCommand']): Promise<boolean> {
    if (this.initial_delivery_cancelled) throw new backend.DeliveryCancelled();
    if (command.operation === 'write_prompt')
      this.delivery_commands = this.delivery_commands.filter(
        (earlier) => earlier.operation === 'start',
      );
    this.delivery_commands.push(command);
    const put = this.commands.put(command);
    this.initial_puts.add(put);
    void put.then(
      () => this.initial_puts.delete(put),
      () => this.initial_puts.delete(put),
    );
    return await put;
  }
  async start(
    start: ResolvedConversationStart,
    options: { vendor_session_cursor: string | null },
  ): Promise<void> {
    let snapshot: WireTypes['CanonicalSkillSnapshot'];
    try {
      snapshot = this.skill_snapshot_supplier(start);
      validate_skill_snapshot(snapshot);
    } catch (error) {
      throw new backend.BackendSpawnFailed(
        `Canonical skill snapshot unavailable: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    let workspace_home_relative: string | null = null;
    if (start.workspace_path_policy === WorkspacePathPolicy.home_relative) {
      const path = relative(
        resolve_path_sync(homedir()),
        resolve_path_sync(start.workspace_folder),
      );
      if (path !== '..' && !path.startsWith('..' + sep) && !isAbsolute(path))
        workspace_home_relative = path;
    } else if (start.workspace_path_policy === WorkspacePathPolicy.executor_default) {
      const path = pure_posix_path(start.workspace_folder);
      if (!path.startsWith('/'))
        throw new ValueError(`${inspect(path)} is not in the subpath of '/'`);
      workspace_home_relative = path.replace(/^\/+/, '') || null;
    }
    const command = this.command({
      kind: 'start',
      canonical_skill_snapshot: snapshot,
      resolved_start: {
        ...start,
        role_materials:
          start.role_materials === null
            ? null
            : {
                ...start.role_materials,
                identity_environment_variables:
                  start.role_materials.identity_environment_variables.map(([name, value]) => [
                    name,
                    value,
                  ]),
              },
      },
      vendor_session_cursor: options.vendor_session_cursor,
      workspace_home_relative,
    });
    let placed: boolean;
    try {
      placed = await this.put_initial(command);
    } catch (error) {
      if (error instanceof ProtocolRefused) throw new backend.BackendSpawnFailed(error.message);
      throw error;
    }
    if (!placed) throw new backend.BackendSpawnFailed(REPLACED_MESSAGE);
    this.note_start(start);
  }
  async write_prompt(
    turn: TurnToken,
    content: MessageContent,
    options: Parameters<BackendChild['write_prompt']>[2],
  ): Promise<backend.BackendPromptAccepted> {
    if (this.claude_started_on !== null) {
      const [model, effort] = this.claude_started_on;
      if (
        (options.model_change !== null && options.model_change !== model) ||
        ((options.reasoning_effort_change !== null || options.reasoning_effort_is_set === true) &&
          options.reasoning_effort_change !== effort)
      )
        throw new backend.NeedsRebind(
          'claude takes its model and reasoning effort when the child starts',
        );
    }
    const command = this.command(
      {
        kind: 'write_prompt',
        content: [...content],
        sender_label: options.sender_label,
        sender_content: [...options.sender_content],
        sender_message_count: options.sender_message_count ?? 1,
        mode: options.mode,
        model_change: options.model_change,
        reasoning_effort_change: options.reasoning_effort_change,
        reasoning_effort_is_set: options.reasoning_effort_is_set ?? false,
        automatic_compaction: options.automatic_compaction ?? false,
      },
      turn,
    );
    let placed: boolean;
    try {
      placed = await this.put_initial(command);
    } catch (error) {
      if (error instanceof ProtocolRefused) throw new backend.PromptWriteRefused(error.message);
      throw error;
    }
    if (!placed) throw new backend.PromptWriteRefused(REPLACED_MESSAGE);
    return new backend.BackendPromptAccepted({ composed_content_delivered: true });
  }
  async steer(
    turn: TurnToken,
    content: MessageContent,
    options: Parameters<BackendChild['steer']>[2],
  ): Promise<backend.BackendSteerOutcome> {
    const not_delivered = new backend.BackendSteerRefused({
      refusal_reason: PromptDeliveryRefusalReason.remote_steer_not_delivered,
    });
    if (!this.commands.mac_is_present()) return not_delivered;
    let outcome: WireTypes['CommandOutcome'] | LateSteer | null;
    try {
      outcome = await this.commands.deliver_steer(
        this.command(
          {
            kind: 'steer',
            content: [...content],
            sender_content: options.sender_content == null ? null : [...options.sender_content],
            sender_label: options.sender_label,
          },
          turn,
        ),
      );
    } catch (error) {
      if (error instanceof ProtocolRefused) return not_delivered;
      throw error;
    }
    if (outcome === null) return not_delivered;
    if (outcome instanceof LateSteer)
      return new backend.BackendSteerUncertain({
        composed_content_delivered: null,
        not_delivered: outcome.not_delivered,
      });
    const delivered = outcome.result.composed_content_delivered;
    if (outcome.disposition === 'uncertain')
      return new backend.BackendSteerUncertain({
        composed_content_delivered: typeof delivered === 'boolean' ? delivered : null,
      });
    raise_for(outcome);
    const result = outcome.result;
    if (!Object.hasOwn(result, 'kind')) throw new KeyError('kind');
    if (result.kind === 'steer_refused') {
      if (!Object.hasOwn(result, 'refusal_reason')) throw new KeyError('refusal_reason');
      const reason =
        typeof result.refusal_reason === 'string'
          ? result.refusal_reason
          : inspect(result.refusal_reason);
      if (
        !Object.values(PromptDeliveryRefusalReason).includes(reason as PromptDeliveryRefusalReason)
      )
        throw new ValueError(`${inspect(reason)} is not a valid PromptDeliveryRefusalReason`);
      return new backend.BackendSteerRefused({
        refusal_reason: reason as PromptDeliveryRefusalReason,
      });
    }
    if (result.kind === 'steer_uncertain')
      return new backend.BackendSteerUncertain({
        composed_content_delivered: typeof delivered === 'boolean' ? delivered : null,
      });
    return new backend.BackendSteerAccepted({
      composed_content_delivered: Object.hasOwn(result, 'composed_content_delivered')
        ? python_truth(delivered)
        : true,
    });
  }
  async cancel_running_turn(): Promise<void> {
    await this.commands.put(this.command({ kind: 'cancel_running_turn' }));
  }
  private async save_answer(answer: WireTypes['DurableAnswer']): Promise<AnswerFate> {
    const arguments_: ChildCommandArguments & { kind: ChildOperation } =
      answer.ask.kind === 'permission'
        ? {
            kind: 'answer_permission_ask',
            ask_id: answer.ask.ask_id,
            option_id: answer.option_id || '',
            answer,
          }
        : {
            kind: 'answer_user_input',
            request_id: answer.ask.ask_id,
            answers: answer.user_input_answers || [],
            answer,
          };
    const command: WireTypes['ChildCommand'] = {
      command_id: answer.answer_id,
      child: this.reference,
      operation: arguments_.kind,
      turn: answer.ask.turn,
      arguments: arguments_,
      file_dependencies: [],
    };
    let saved: boolean;
    try {
      saved = await this.commands.save_answer(command);
    } catch (error) {
      if (error instanceof ProtocolRefused) return AnswerFate.refused;
      throw error;
    }
    return saved ? AnswerFate.saved : AnswerFate.refused;
  }
  async answer_permission_ask(
    ask_id: string,
    option_id: string,
    options: { answer_id?: string | null; turn_token?: TurnToken | null } = {},
  ): Promise<AnswerFate> {
    if (options.answer_id == null || options.turn_token == null) return AnswerFate.refused;
    return this.save_answer({
      answer_id: options.answer_id,
      ask: { child: this.reference, turn: options.turn_token, ask_id, kind: 'permission' },
      option_id,
      accepted_at_vps_ms: now_ms(),
      user_input_answers: null,
    });
  }
  async answer_user_input(
    request_id: string,
    answers: readonly UserInputAnswer[],
    options: { answer_id?: string | null; turn_token?: TurnToken | null } = {},
  ): Promise<AnswerFate> {
    if (options.answer_id == null || options.turn_token == null) return AnswerFate.refused;
    return this.save_answer({
      answer_id: options.answer_id,
      ask: {
        child: this.reference,
        turn: options.turn_token,
        ask_id: request_id,
        kind: 'user_input',
      },
      option_id: null,
      accepted_at_vps_ms: now_ms(),
      user_input_answers: answers.map((answer) => ({
        question_id: answer.question_id,
        answers: [...answer.answers],
      })),
    });
  }
  settled(): Promise<void> {
    return this.stop_settlement;
  }
  private observe_stop(task: Promise<void>, options?: StopOptions): Promise<void> {
    return options === undefined ? task : observe_shutdown(task, options, 'remote child stop');
  }
  stop(options?: StopOptions): Promise<void> {
    if (this.stop_task === null) {
      const task = (async () => {
        raise_for(await this.commands.stop(this.command({ kind: 'stop' })));
      })();
      this.stop_task = task;
      this.stop_settlement = task;
      void task.catch(() => {
        if (this.stop_task === task) this.stop_task = null;
      });
    }
    return this.observe_stop(this.stop_task, options);
  }
}
