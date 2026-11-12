/** Demand-only output spools and bounded, contentless view wakes. */
import { createHash, randomUUID } from 'node:crypto';
import {
  appendFileSync,
  closeSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import * as event from '../events.ts';
import type {
  ConversationViewLens,
  ViewBodyChunk,
  ViewBodyReference,
  ViewLiveOutput,
} from './contracts.ts';
import { BODY_CHUNK_BYTES, SUBSCRIBER_CHANGE_LIMIT } from './configuration.ts';
import { ViewBodyChanged } from './data/storage.ts';
interface LiveConversation {
  turn_key: string | null;
  revision: number;
  activity_at: number | null;
  spool: string | null;
  spool_bytes: number;
  output_generation: number;
  latest_tool_id: string | null;
}
export class ViewSubscription {
  readonly owner: ConversationViewLiveOutput;
  readonly conversation_id: string;
  readonly lens: ConversationViewLens;
  readonly transcript: boolean;
  pending_changes = 0;
  overflow = false;
  closed = false;
  #wake = false;
  #waiting: (() => void) | null = null;
  constructor(
    owner: ConversationViewLiveOutput,
    conversation_id: string,
    lens: ConversationViewLens,
    transcript: boolean,
  ) {
    this.owner = owner;
    this.conversation_id = conversation_id;
    this.lens = lens;
    this.transcript = transcript;
  }
  notify(options: { durable: boolean }): void {
    if (this.closed) {
      return;
    }
    if (options.durable) {
      this.pending_changes++;
      if (this.pending_changes > SUBSCRIBER_CHANGE_LIMIT) {
        this.overflow = true;
      }
    }
    this.#signal();
  }
  #signal(): void {
    this.#wake = true;
    this.#waiting?.();
    this.#waiting = null;
  }
  async next_wake(timeout_ms?: number): Promise<boolean | null> {
    if (!this.#wake) {
      const woke = await new Promise<boolean>((resolve) => {
        let timer: ReturnType<typeof setTimeout> | undefined;
        this.#waiting = () => {
          if (timer) {
            clearTimeout(timer);
          }
          resolve(true);
        };
        if (timeout_ms !== undefined) {
          timer = setTimeout(() => {
            this.#waiting = null;
            resolve(false);
          }, timeout_ms);
        }
      });
      if (!woke) {
        return null;
      }
    }
    this.#wake = false;
    this.pending_changes = 0;
    return !this.closed && !this.overflow;
  }
  close(): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.owner.forget(this);
    this.#signal();
  }
}
export interface OpenLiveContent {
  fd: number;
  close(): void;
}
export class ConversationViewLiveOutput {
  readonly runtime_epoch = randomUUID().replaceAll('-', '');
  #directory: string | undefined;
  #temporary = false;
  readonly #conversations = new Map<string, LiveConversation>();
  readonly #subscriptions = new Map<string, Set<ViewSubscription>>();
  constructor(directory?: string) {
    this.#directory = directory;
  }
  cleanup_stale_epochs(): void {
    if (this.#directory === undefined) {
      return;
    }
    mkdirSync(this.#directory, { recursive: true });
    for (const child of readdirSync(this.#directory, { withFileTypes: true })) {
      if (
        child.isDirectory() &&
        child.name.startsWith('epoch-') &&
        child.name !== `epoch-${this.runtime_epoch}`
      ) {
        rmSync(join(this.#directory, child.name), { recursive: true, force: true });
      }
    }
  }
  #root(): string {
    if (this.#directory === undefined) {
      this.#directory = mkdtempSync(join(tmpdir(), 'panels-view-output-'));
      this.#temporary = true;
    }
    const root = join(this.#directory, `epoch-${this.runtime_epoch}`);
    mkdirSync(root, { recursive: true });
    return root;
  }
  static #identity(value: string): string {
    return createHash('sha256').update(value).digest('hex');
  }
  #state(conversation_id: string, turn_key: string | null = null): LiveConversation {
    let state = this.#conversations.get(conversation_id);
    if (state === undefined) {
      state = {
        turn_key,
        revision: 0,
        activity_at: null,
        spool: null,
        spool_bytes: 0,
        output_generation: 0,
        latest_tool_id: null,
      };
      this.#conversations.set(conversation_id, state);
    }
    return state;
  }
  subscribe(
    conversation_id: string,
    lens: ConversationViewLens,
    options: {
      transcript: boolean;
      turn_key: string | null;
    },
  ): ViewSubscription {
    const subscription = new ViewSubscription(this, conversation_id, lens, options.transcript);
    let watchers = this.#subscriptions.get(conversation_id);
    if (watchers === undefined) {
      watchers = new Set();
      this.#subscriptions.set(conversation_id, watchers);
    }
    watchers.add(subscription);
    const state = this.#state(conversation_id, options.turn_key);
    if (state.turn_key !== options.turn_key) {
      this.#reset_turn(conversation_id, options.turn_key);
    }
    if (lens === 'full' && options.transcript && options.turn_key !== null) {
      this.#ensure_spool(conversation_id);
    }
    return subscription;
  }
  forget(subscription: ViewSubscription): void {
    const watchers = this.#subscriptions.get(subscription.conversation_id);
    watchers?.delete(subscription);
    if (watchers?.size === 0) {
      this.#subscriptions.delete(subscription.conversation_id);
    }
    const state = this.#conversations.get(subscription.conversation_id);
    if (state && state.spool === null && !watchers?.size) {
      this.#conversations.delete(subscription.conversation_id);
    }
  }
  #reset_turn(conversation_id: string, turn_key: string | null): void {
    const state = this.#state(conversation_id);
    if (state.spool !== null) {
      rmSync(dirname(state.spool), { recursive: true, force: true });
    }
    Object.assign(state, { turn_key, spool: null, spool_bytes: 0, latest_tool_id: null });
    state.output_generation++;
    state.revision++;
  }
  #ensure_spool(conversation_id: string): void {
    const state = this.#state(conversation_id);
    if (state.spool !== null || state.turn_key === null) {
      return;
    }
    const folder = join(this.#root(), ConversationViewLiveOutput.#identity(conversation_id));
    mkdirSync(folder, { recursive: true });
    state.spool = join(folder, 'output.txt');
    const fd = openSync(state.spool, 'a');
    closeSync(fd);
  }
  static #replace_content(path: string, contents: Uint8Array): void {
    writeFileSync(path + '.replacement', contents);
    renameSync(path + '.replacement', path);
  }
  publish_event(
    conversation_id: string,
    sequence: number,
    payload: event.ConversationEventPayload,
  ): void {
    const state = this.#conversations.get(conversation_id);
    if (state !== undefined) {
      if (payload.kind === 'prompt' && state.turn_key === null) {
        this.#reset_turn(conversation_id, `turn:e${sequence}`);
        if (
          [...(this.#subscriptions.get(conversation_id) ?? [])].some(
            (w) => w.lens === 'full' && w.transcript,
          )
        ) {
          this.#ensure_spool(conversation_id);
        }
      } else if (payload.kind === 'turn_ended') {
        this.#reset_turn(conversation_id, null);
        if (!this.#subscriptions.get(conversation_id)?.size) {
          this.#conversations.delete(conversation_id);
        }
      } else if (payload.kind === 'agent_message' && state.spool !== null) {
        ConversationViewLiveOutput.#replace_content(state.spool, Buffer.alloc(0));
        state.spool_bytes = 0;
        state.output_generation++;
        state.revision++;
      } else if (payload.kind === 'tool_call_finished' && state.spool !== null) {
        rmSync(
          join(
            dirname(state.spool),
            'tool-' + ConversationViewLiveOutput.#identity(payload.tool_call_id),
          ),
          { force: true },
        );
        if (state.latest_tool_id === payload.tool_call_id) {
          state.latest_tool_id = null;
        }
      }
      state.revision++;
    }
    for (const watcher of [...(this.#subscriptions.get(conversation_id) ?? [])]) {
      watcher.notify({ durable: true });
    }
  }
  publish_frame(conversation_id: string, frame: event.ConversationLiveTailFrame): void {
    const state = this.#conversations.get(conversation_id);
    if (state === undefined) {
      return;
    }
    state.activity_at = Date.now();
    state.revision++;
    if (state.spool !== null) {
      if (frame instanceof event.AgentMessageDeltaFrame) {
        const encoded = Buffer.from(frame.text_delta);
        appendFileSync(state.spool, encoded);
        state.spool_bytes += encoded.byteLength;
      } else if (frame instanceof event.ToolCallProgressFrame) {
        ConversationViewLiveOutput.#replace_content(
          join(
            dirname(state.spool),
            'tool-' + ConversationViewLiveOutput.#identity(frame.tool_call_id),
          ),
          Buffer.from(frame.detail),
        );
        state.latest_tool_id = frame.tool_call_id;
      }
    }
    for (const watcher of [...(this.#subscriptions.get(conversation_id) ?? [])]) {
      watcher.notify({ durable: false });
    }
  }
  publish_status_changed(conversation_id: string): void {
    const state = this.#conversations.get(conversation_id);
    if (state) {
      state.revision++;
    }
    for (const watcher of [...(this.#subscriptions.get(conversation_id) ?? [])]) {
      watcher.notify({ durable: false });
    }
  }
  status(conversation_id: string): readonly [number, number | null] {
    const state = this.#conversations.get(conversation_id);
    return state ? [state.revision, state.activity_at] : [0, null];
  }
  output(conversation_id: string): ViewLiveOutput | null {
    const state = this.#conversations.get(conversation_id);
    if (!state?.spool || state.turn_key === null) {
      return null;
    }
    return {
      runtime_epoch: this.runtime_epoch,
      turn_key: state.turn_key,
      version: state.revision,
      byte_count: state.spool_bytes,
      reference: {
        body_id: 'live',
        version: `${this.runtime_epoch}:${state.turn_key}:${state.output_generation}`,
        byte_count: state.spool_bytes,
        media_type: 'text/markdown',
      },
    };
  }
  progress(conversation_id: string, tool_call_id?: string | null): ViewBodyReference | null {
    const state = this.#conversations.get(conversation_id);
    const identity = tool_call_id || state?.latest_tool_id;
    if (!state?.spool || !identity) {
      return null;
    }
    const path = join(
      dirname(state.spool),
      'tool-' + ConversationViewLiveOutput.#identity(identity),
    );
    if (!existsSync(path)) {
      return null;
    }
    const stat = statSync(path, { bigint: true });
    return {
      body_id: 'progress:' + ConversationViewLiveOutput.#identity(identity),
      version: `${this.runtime_epoch}:${stat.mtimeNs}`,
      byte_count: Number(stat.size),
      media_type: 'text/plain',
    };
  }
  latest_tool_id(conversation_id: string): string | null {
    return this.#conversations.get(conversation_id)?.latest_tool_id ?? null;
  }
  #content_path(conversation_id: string, reference: ViewBodyReference): string {
    const state = this.#conversations.get(conversation_id);
    if (!state?.spool) {
      throw new ViewBodyChanged('Live output belongs to an ended runtime turn');
    }
    if (reference.body_id === 'live') {
      const current = this.output(conversation_id);
      if (
        !current ||
        reference.version !== current.reference.version ||
        reference.byte_count > current.byte_count
      ) {
        throw new ViewBodyChanged('Live output source changed');
      }
      return state.spool;
    }
    if (reference.body_id.startsWith('progress:')) {
      const digest = reference.body_id.slice('progress:'.length);
      if (!/^[0-9a-f]{64}$/.test(digest)) {
        throw new Error('Invalid progress identity');
      }
      const path = join(dirname(state.spool), 'tool-' + digest);
      if (
        !existsSync(path) ||
        reference.version !== `${this.runtime_epoch}:${statSync(path, { bigint: true }).mtimeNs}`
      ) {
        throw new ViewBodyChanged('Tool progress source changed');
      }
      return path;
    }
    throw new Error('Unsupported live body');
  }
  read_chunk(conversation_id: string, reference: ViewBodyReference, offset: number): ViewBodyChunk {
    const path = this.#content_path(conversation_id, reference);
    if (offset < 0 || offset > reference.byte_count) {
      throw new Error('Live body offset is outside the source');
    }
    const fd = openSync(path, 'r');
    let encoded: Buffer;
    try {
      encoded = Buffer.alloc(Math.min(BODY_CHUNK_BYTES + 3, reference.byte_count - offset));
      encoded = encoded.subarray(0, readSync(fd, encoded, 0, encoded.length, offset));
    } finally {
      closeSync(fd);
    }
    let end = Math.min(BODY_CHUNK_BYTES, encoded.length);
    while (end < encoded.length && (encoded[end] & 0xc0) === 0x80) {
      end--;
    }
    let text: string;
    try {
      text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
        encoded.subarray(0, end),
      );
    } catch {
      throw new Error('Live body offset is not a UTF-8 boundary');
    }
    const next_offset = offset + end;
    return {
      reference,
      offset,
      text,
      next_offset: next_offset < reference.byte_count ? next_offset : null,
    };
  }
  open_content(conversation_id: string, reference: ViewBodyReference): OpenLiveContent {
    this.read_chunk(conversation_id, reference, 0);
    const fd = openSync(this.#content_path(conversation_id, reference), 'r');
    return { fd, close: () => closeSync(fd) };
  }
  close(): void {
    for (const watchers of [...this.#subscriptions.values()]) {
      for (const watcher of [...watchers]) {
        watcher.close();
      }
    }
    for (const id of [...this.#conversations.keys()]) {
      this.#reset_turn(id, null);
    }
    this.#conversations.clear();
    if (this.#directory !== undefined) {
      rmSync(
        this.#temporary ? this.#directory : join(this.#directory, `epoch-${this.runtime_epoch}`),
        { recursive: true, force: true },
      );
    }
  }
}
