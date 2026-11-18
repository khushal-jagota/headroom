/** One ACP session's model and reasoning effort, set through its config options, and its wire state. */
import { RequestError } from '@agentclientprotocol/sdk/unstable';
import type {
  NewSessionResponse,
  SessionConfigOption,
  SetSessionConfigOptionResponse,
} from '@agentclientprotocol/sdk/unstable';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import {
  NeedsRebind,
  PromptWriteFailed,
  SessionLoadFailed,
} from '../../conversation/backends/contracts.ts';
import { error_text } from '../../conversation/backends/failure_summary.ts';
import type { AcpClient } from './client.ts';
import { CONTROL_REQUEST_TIMEOUT_SECONDS } from './configuration.ts';

/** The agent's own error text. A closed connection reads the same whichever side noticed it. */
export function acp_error_text(error: unknown): string {
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
export class AcpSessionConfiguration {
  session_id: string | null = null;
  /** What this child last set. Null: the agent's own choice, never set by Panels. */
  model: string | null = null;
  reasoning_effort: string | null = null;
  wire_broken = false;
  private wire_failure: { why: string; cause: unknown } | null = null;
  private options: readonly SessionConfigOption[] = [];
  readonly client: AcpClient;
  private readonly label: string;
  constructor(client: AcpClient, label: string) {
    this.client = client;
    this.label = label;
  }
  bind(session_id: string, response: Pick<NewSessionResponse, 'configOptions'>): void {
    this.session_id = session_id;
    this.options = response.configOptions ?? [];
  }
  /** Keep the first reason the wire failed, so a later refusal can say why. */
  break_wire(cause: unknown): void {
    this.wire_broken = true;
    this.wire_failure ??= { why: acp_error_text(cause), cause };
  }
  /** Whether this child can take no further request: its wire failed or its process ended. */
  get wire_ended(): boolean {
    return this.wire_broken || this.client.signal.aborted;
  }
  /** Why the wire ended, for a refusal. */
  wire_failure_text(): { why: string | null; cause: unknown } {
    const failure =
      this.wire_failure ??
      (this.client.signal.aborted && this.client.signal.reason !== undefined
        ? { why: acp_error_text(this.client.signal.reason), cause: this.client.signal.reason }
        : null);
    return failure ?? { why: null, cause: undefined };
  }
  require_wire(): void {
    if (!this.wire_ended) return;
    const failure = this.wire_failure_text();
    throw new PromptWriteFailed(
      failure.why === null
        ? "this child's wire has already failed"
        : `this child's wire has already failed: ${failure.why}`,
      failure.why === null ? undefined : { cause: failure.cause },
    );
  }
  bound_session(): string {
    if (this.session_id === null) throw new PromptWriteFailed('this child has no bound session');
    return this.session_id;
  }
  async guarded<T>(call: () => Promise<T>): Promise<T> {
    try {
      return await call();
    } catch (error) {
      this.break_wire(error);
      throw new PromptWriteFailed(acp_error_text(error), { cause: error });
    }
  }
  private async configuration_request<T>(method: string, params: unknown): Promise<T> {
    const controller = new AbortController();
    let timed_out = false;
    const timer = setTimeout(() => {
      timed_out = true;
      controller.abort(new Error(`the ${this.label} configuration request timed out`));
    }, CONTROL_REQUEST_TIMEOUT_SECONDS * 1000);
    try {
      return await this.client.request<T>(method, params, { signal: controller.signal });
    } catch (error) {
      // A stalled ordered reader must not close the healthy wire or fail other requests. A
      // reply that did not arrive leaves the applied value unknown, so the core must retire
      // this child before another prompt.
      if (timed_out)
        throw new NeedsRebind(
          `the ${this.label} configuration request timed out before its result was known`,
          { cause: error },
        );
      if (!(error instanceof RequestError)) this.break_wire(error);
      throw new ConfigurationNotApplied(acp_error_text(error), { cause: error });
    } finally {
      clearTimeout(timer);
    }
  }
  async start_values(start: ResolvedConversationStart): Promise<void> {
    try {
      await this.apply_values(start.model, start.reasoning_effort);
    } catch (error) {
      if (!(error instanceof ConfigurationNotApplied) && !(error instanceof NeedsRebind))
        throw error;
      throw new SessionLoadFailed(error.message, { cause: error });
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
      throw new ConfigurationNotApplied(error.message, { cause: error });
    }
  }
  /** The agent answers with every option again: a new model can bring other effort values. */
  private async set_option(configId: string, value: string): Promise<void> {
    const sessionId = this.config_session();
    const response = await this.configuration_request<SetSessionConfigOptionResponse>(
      'session/set_config_option',
      { configId, sessionId, value },
    );
    if (response?.configOptions?.length) this.options = response.configOptions;
  }
  private async apply_model(model: string): Promise<void> {
    if (model === this.model) return;
    const option = this.option('model');
    if (!option)
      throw new ConfigurationNotApplied(
        `this session advertises no model option, so it cannot be put on ${JSON.stringify(model)}`,
      );
    await this.set_option(option.id, model);
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
      throw new NeedsRebind(error.message, { cause: error });
    }
  }
}
