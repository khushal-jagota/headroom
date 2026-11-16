/** Bounds and SDK contract retained from Python's Hermes ACP adapter. */
export const ACP_SDK_VERSION = '2.6.0';
export const CHILD_OUTPUT_LINE_LIMIT_BYTES = 50 * 1024 * 1024;
export const STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS = 8192;
export const CANCELLED_TURN_ENDING_TIMEOUT_SECONDS = 15;
export const ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS = 15;
export const AVAILABLE_COMMANDS_WAIT_TIMEOUT_SECONDS = 15;
export const CONTROL_REQUEST_TIMEOUT_SECONDS = 15;

import { RequestError } from '@agentclientprotocol/sdk/unstable';
import type {
  LoadSessionResponse,
  SessionConfigOption,
  SetSessionConfigOptionResponse,
} from '@agentclientprotocol/sdk/unstable';
import { ConversationAccess } from '../../contracts.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';
import { NeedsRebind, PromptWriteFailed, SessionLoadFailed } from '../contracts.ts';
import { error_text } from '../claude_agent_sdk/failure_summary.ts';
import type { HermesAcpClient } from './client.ts';

export function hermes_error_text(error: unknown): string {
  // The Python ACP connection raises ConnectionError with this exact text at EOF.
  // Preserve a provider's stated RequestError text, even when it matches the SDK.
  if (
    !(error instanceof RequestError) &&
    error instanceof Error &&
    (error.message === 'ACP connection closed' || error.message === 'the backend process ended')
  )
    return 'Connection closed';
  return error_text(error);
}
export class ConfigurationNotApplied extends Error {}
/** Python's carried model/effort values, including its logical broken-wire state. */
export class HermesConfiguration {
  session_id: string | null = null;
  model: string | null = null;
  reasoning_effort: string | null = null;
  wire_broken = false;
  private options: readonly SessionConfigOption[] = [];
  readonly client: HermesAcpClient;
  constructor(client: HermesAcpClient) {
    this.client = client;
  }
  bind(session_id: string, response: LoadSessionResponse): void {
    this.session_id = session_id;
    this.options = response.configOptions ?? [];
    const panels = response._meta?.panels;
    const value =
      panels && typeof panels === 'object' && 'currentModelId' in panels
        ? panels.currentModelId
        : null;
    this.model = typeof value === 'string' && value ? value : null;
  }
  require_wire(): void {
    if (this.wire_broken || this.client.signal.aborted)
      throw new PromptWriteFailed("this child's wire has already failed");
  }
  bound_session(): string {
    if (this.session_id === null) throw new PromptWriteFailed('this child has no bound session');
    return this.session_id;
  }
  async guarded<T>(call: () => Promise<T>): Promise<T> {
    try {
      return await call();
    } catch (error) {
      this.wire_broken = true;
      throw new PromptWriteFailed(hermes_error_text(error));
    }
  }
  async control_request<T>(method: string, params: unknown, configuration = false): Promise<T> {
    const controller = new AbortController();
    let timed_out = false;
    const timer = setTimeout(() => {
      timed_out = true;
      controller.abort(new Error('the Hermes control request timed out'));
    }, CONTROL_REQUEST_TIMEOUT_SECONDS * 1000);
    try {
      return await this.client.request<T>(method, params, { signal: controller.signal });
    } catch (error) {
      // A stalled ordered reader must not close the healthy wire or fail other
      // requests. A configuration reply that did not arrive leaves applied state
      // unknown, so the core must retire this child before another prompt.
      if (timed_out) {
        if (configuration)
          throw new NeedsRebind(
            'the Hermes configuration request timed out before its result was known',
          );
        throw new PromptWriteFailed(
          'the Hermes steer request timed out before its result was known',
        );
      }
      this.wire_broken = true;
      throw new PromptWriteFailed(hermes_error_text(error));
    } finally {
      clearTimeout(timer);
    }
  }
  async start_values(start: ResolvedConversationStart): Promise<void> {
    try {
      if (start.access === ConversationAccess.full) {
        const sessionId = this.config_session();
        await this.config_call('session/set_mode', { sessionId, modeId: 'dont_ask' });
      }
      await this.apply_values(start.model, start.reasoning_effort);
    } catch (error) {
      if (!(error instanceof ConfigurationNotApplied) && !(error instanceof NeedsRebind))
        throw error;
      throw new SessionLoadFailed(error.message);
    }
  }
  async apply_values(model: string | null, effort: string | null): Promise<void> {
    if (model !== null) await this.apply_model(model);
    if (effort !== null) await this.apply_effort(effort);
  }
  private option(category: string): SessionConfigOption | undefined {
    return this.options.find((option) => option.type === 'select' && option.category === category);
  }
  private config_session(): string {
    try {
      const session = this.bound_session();
      this.require_wire();
      return session;
    } catch (error) {
      if (!(error instanceof PromptWriteFailed)) throw error;
      throw new ConfigurationNotApplied(error.message);
    }
  }
  private async config_call<T>(method: string, params: unknown): Promise<T> {
    try {
      return await this.control_request<T>(method, params, true);
    } catch (error) {
      if (!(error instanceof PromptWriteFailed)) throw error;
      throw new ConfigurationNotApplied(error.message);
    }
  }
  private async set_option(configId: string, value: string): Promise<void> {
    const sessionId = this.config_session();
    const response = await this.config_call<SetSessionConfigOptionResponse>(
      'session/set_config_option',
      { configId, sessionId, value },
    );
    if (response?.configOptions?.length) this.options = response.configOptions;
  }
  private async apply_model(model: string): Promise<void> {
    if (model === this.model) return;
    const option = this.option('model');
    if (option) await this.set_option(option.id, model);
    else {
      const sessionId = this.config_session();
      await this.config_call('session/set_model', { sessionId, modelId: model });
    }
    this.model = model;
  }
  private async apply_effort(effort: string): Promise<void> {
    const option = this.option('thought_level');
    if (!option)
      throw new ConfigurationNotApplied(
        `this session advertises no reasoning-effort option, so it cannot be put on ${JSON.stringify(effort)}`,
      );
    await this.set_option(option.id, effort);
    this.reasoning_effort = effort;
  }
  async restore(previously: readonly [string | null, string | null]): Promise<void> {
    const [model, effort] = previously;
    try {
      if (model !== this.model) {
        if (model === null)
          throw new ConfigurationNotApplied(
            "the session was on the agent's own model and there is no way to ask for that again",
          );
        await this.apply_model(model);
      }
      if (effort !== this.reasoning_effort) {
        if (effort === null)
          throw new ConfigurationNotApplied(
            "the session was on the agent's own reasoning effort and there is no way to ask for that again",
          );
        await this.apply_effort(effort);
      }
    } catch (error) {
      if (!(error instanceof ConfigurationNotApplied)) throw error;
      throw new NeedsRebind(error.message);
    }
  }
}
