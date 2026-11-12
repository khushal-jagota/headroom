/** Manual, one-send proof. Import does not select or start any provider executable. */
import childProcess from 'node:child_process';
import type { ChildProcess, SpawnOptions } from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { StringDecoder } from 'node:string_decoder';
import type { SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import { createDatabase } from '../../../core/database/index.ts';
import { register_test_root } from '../../../core/test_paths.ts';
import { ConversationStore } from '../../record/data/store.ts';
import { ConversationMessageFiles } from '../../record/message_files.ts';
import { SqliteProcessConversationSystem } from '../../system.ts';
import { ConversationStartRequest } from '../../contracts.ts';
import { AgentMessageDeltaFrame } from '../../events.ts';
import { text_message_content, message_content_text } from '../../message_content.ts';
import { delivery_fate_json } from '../../http.ts';
import { record } from '../../backends/claude_agent_sdk/events.ts';
import type {
  ClaudeSdkClientFactory,
  ClaudeSdkMessage,
} from '../../backends/claude_agent_sdk/query_contracts.ts';
import { bounded } from '../../backends/claude_agent_sdk/wire.ts';

export interface Part3Spec {
  database: string;
  workspace: string;
  executable: string;
  conversation_id: string;
  mode: 'fresh' | 'resume';
  prompt: string;
  expected_cursor?: string;
  claude_home?: string;
  trace?: string;
  scripted?: boolean;
}
interface LaunchEvidence {
  command: string;
  arguments: readonly string[];
  probe: boolean;
  pid: number | null;
  closed: boolean;
  exit_code: number | null;
  signal: NodeJS.Signals | null;
  version: string | null;
}
function inside(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return suffix !== '..' && !suffix.startsWith(`..${sep}`) && !isAbsolute(suffix);
}
function failure_kind(detail: string): string {
  if (/spend|credit|balance|usage limit|limit.*reached|reached.*limit/i.test(detail))
    return 'spend_limit';
  if (/HTTP 401|authentication|not logged in|login required|unauthorized/i.test(detail))
    return 'authentication';
  if (/version|too old|at least 2\.1\.219/i.test(detail)) return 'version';
  return 'turn_failed';
}
function type_name(value: unknown): string {
  return value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
}
function native_shape(message: ClaudeSdkMessage): Record<string, unknown> {
  const body = record(message.message);
  return {
    type: message.type,
    ...(typeof message.subtype === 'string' ? { subtype: message.subtype } : {}),
    fields: Object.fromEntries(
      Object.entries(message).map(([key, value]) => [key, type_name(value)]),
    ),
    ...(body
      ? {
          message_fields: Object.fromEntries(
            Object.entries(body).map(([key, value]) => [key, type_name(value)]),
          ),
        }
      : {}),
    ...(Array.isArray(body?.content)
      ? {
          content_blocks: body.content.map((value) => {
            const block = record(value);
            return block
              ? { type: block.type, fields: Object.keys(block).sort() }
              : { type: type_name(value) };
          }),
        }
      : {}),
    ...(record(message.usage) ? { usage_fields: Object.keys(message.usage as object).sort() } : {}),
  };
}

export async function run_part3_turn(spec: Part3Spec): Promise<unknown> {
  for (const name of Object.keys(process.env)) {
    if (name.startsWith('PLAN_'))
      throw new Error('Manual real proof refuses inherited Panels identity');
  }
  if (!isAbsolute(spec.executable) || !isAbsolute(spec.database) || !isAbsolute(spec.workspace))
    throw new Error('Manual proof paths must be explicit absolute paths');
  const root = dirname(resolve(spec.database));
  if (!inside(root, resolve(spec.workspace)) || (spec.trace && !inside(root, resolve(spec.trace))))
    throw new Error('Manual proof state must share its scratch root');
  if (
    !spec.scripted &&
    ![
      '/home/vps/projects/panels/task-tmp/t_02cfxhf1',
      '/home/vps/Deployments/Panels/current/data/files/tickets/t_02cfxhf1',
    ].some((ticketRoot) => inside(ticketRoot, root))
  )
    throw new Error('Real proof state must stay in this Ticket tree');
  if (spec.mode === 'resume' && !spec.expected_cursor)
    throw new Error('Resume requires its exact allowlisted core cursor');
  if (spec.mode === 'fresh' && spec.expected_cursor)
    throw new Error('Fresh proof cannot carry a resume cursor');
  const environmentBefore = { ...process.env };
  const started = performance.now();
  const launches: LaunchEvidence[] = [];
  const handles: ChildProcess[] = [];
  const nativeMessages: Record<string, unknown>[] = [];
  const rawNativeMessages: Record<string, unknown>[] = [];
  let rawMalformedLines = 0;
  const nativeText: string[] = [];
  const outgoing: { uuid: string | null; text: string }[] = [];
  const disconnected: { completed: boolean; failure_class: string | null }[] = [];
  let unexpected: string | null = null;
  let promptStarted = false;
  let initObserved = false;
  let initToolsEmpty = false;
  let nativeModel: string | null = null;
  let haikuConfirmed = false;
  let versionProbeStarted = false;
  let cliStarted = false;
  let launchBudgetViolation = false;
  let stopReason: string | null = null;
  let errorClass: string | null = null;
  let resultError: string | null = null;
  let signalUnexpected!: () => void;
  const unexpectedActivity = new Promise<never>((_resolve, reject) => {
    signalUnexpected = () => reject(new Error('Unexpected tool or hook activity in manual proof'));
  });
  void unexpectedActivity.catch(() => {});
  const prohibit = (kind: string): void => {
    unexpected ??= kind;
    signalUnexpected();
  };
  const original = {
    spawn: childProcess.spawn,
    spawnSync: childProcess.spawnSync,
    exec: childProcess.exec,
    execSync: childProcess.execSync,
    execFile: childProcess.execFile,
    execFileSync: childProcess.execFileSync,
    fork: childProcess.fork,
  };
  const denied = (): never => {
    throw new Error('Unexpected process API in the manual proof');
  };
  const spawn = (command: string, arguments_: readonly string[] = [], options?: SpawnOptions) => {
    const direct = command === spec.executable;
    const selectedNode =
      (command === 'node' || command === process.execPath) && arguments_[0] === spec.executable;
    if (!direct && !selectedNode) denied();
    for (const name of Object.keys(options?.env ?? {})) if (name.startsWith('PLAN_')) denied();
    const probe = arguments_.length === 1 && ['--version', '-v'].includes(arguments_[0]!);
    if (probe) {
      if (!direct || arguments_[0] !== '--version' || versionProbeStarted || cliStarted) {
        launchBudgetViolation = true;
        denied();
      }
      versionProbeStarted = true;
    } else {
      if (!versionProbeStarted || cliStarted) {
        launchBudgetViolation = true;
        denied();
      }
      cliStarted = true;
    }
    const child = original.spawn(command, [...arguments_], options ?? {});
    handles.push(child);
    const item: LaunchEvidence = {
      command,
      arguments: [...arguments_],
      probe,
      pid: child.pid ?? null,
      closed: false,
      exit_code: null,
      signal: null,
      version: null,
    };
    launches.push(item);
    if (probe) {
      let output = '';
      child.stdout?.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8');
      });
      child.once('close', () => {
        const match = /^(\S+) \(Claude Code\)$/.exec(output.trim());
        item.version = match?.[1] ?? null;
      });
    } else {
      // Observe this owned child's bytes without replacing streams or modifying input.
      // The public SDK iterator hides control, keep-alive, and mirror frames.
      const decoder = new StringDecoder('utf8');
      let pending = '';
      const observe = (line: string) => {
        if (!line.trim()) return;
        let raw: unknown;
        try {
          raw = JSON.parse(line);
        } catch {
          rawMalformedLines += 1;
          return;
        }
        const message = record(raw);
        if (!message || typeof message.type !== 'string') {
          rawMalformedLines += 1;
          return;
        }
        const observed = native_shape(message as ClaudeSdkMessage);
        if (message.type === 'command_lifecycle') {
          observed.state =
            typeof message.state === 'string' ? message.state : type_name(message.state);
          observed.current_prompt_uuid = outgoing.some(
            (prompt) => prompt.uuid === message.command_uuid,
          );
        }
        if (message.type === 'control_request') {
          const request = record(message.request);
          observed.request_subtype = typeof request?.subtype === 'string' ? request.subtype : null;
          observed.request_fields = request ? Object.keys(request).sort() : [];
          if (request?.subtype === 'can_use_tool' || request?.subtype === 'hook_callback')
            prohibit('raw_tool_or_hook_request');
        }
        if (message.type === 'control_response') {
          const response = record(message.response);
          const payload = record(response?.response);
          observed.response_subtype =
            typeof response?.subtype === 'string' ? response.subtype : null;
          observed.response_fields = payload ? Object.keys(payload).sort() : [];
          observed.cancellation_arrays = Object.fromEntries(
            ['cancelled', 'still_queued']
              .filter((name) => payload && Object.hasOwn(payload, name))
              .map((name) => {
                const values = payload![name];
                return [
                  name,
                  {
                    type: type_name(values),
                    ...(Array.isArray(values)
                      ? { length: values.length, members: values.map(type_name) }
                      : {}),
                  },
                ];
              }),
          );
        }
        if (message.type === 'result') {
          const uuids = [
            ...(Array.isArray(message.user_message_uuids) ? message.user_message_uuids : []),
            message.user_message_uuid,
          ];
          observed.current_prompt_uuid = outgoing.some((prompt) => uuids.includes(prompt.uuid));
        }
        rawNativeMessages.push(observed);
        if (
          message.type === 'system' &&
          ['hook_started', 'hook_progress', 'hook_response'].includes(String(message.subtype))
        )
          prohibit('raw_hook_event');
      };
      const take = (text: string) => {
        pending += text;
        const lines = pending.split('\n');
        pending = lines.pop() ?? '';
        for (const line of lines) observe(line);
      };
      child.stdout?.on('data', (chunk: Buffer) => take(decoder.write(chunk)));
      child.stdout?.once('end', () => {
        take(decoder.end());
        observe(pending);
        pending = '';
      });
    }
    child.once('close', (code, signal) => {
      item.closed = true;
      item.exit_code = code;
      item.signal = signal;
    });
    return child;
  };
  const unregister = register_test_root(root);
  Object.assign(childProcess, {
    spawn,
    spawnSync: denied,
    exec: denied,
    execSync: denied,
    execFile: denied,
    execFileSync: denied,
    fork: denied,
  });
  syncBuiltinESMExports();
  let database: ReturnType<typeof createDatabase> | null = null;
  let core: SqliteProcessConversationSystem | null = null;
  let store: ConversationStore | null = null;
  let shutdownCompleted = false;
  let shutdownFailure: string | null = null;
  let fallbackRequired = false;
  const fallbackCleanup: {
    pid: number | null;
    term_requested: boolean;
    kill_requested: boolean;
    closed: boolean;
    failure_class: string | null;
  }[] = [];
  const waitClosed = async (child: ChildProcess, item: LaunchEvidence): Promise<boolean> => {
    if (item.closed) return true;
    let closed!: () => void;
    const exited = new Promise<void>((resolve) => {
      closed = resolve;
      child.once('close', closed);
    });
    try {
      const result = await bounded(exited, 2);
      return !result.timed_out && item.closed;
    } finally {
      child.off('close', closed);
    }
  };
  let beforeSequence = 0;
  let beforeTurn = 0;
  let cursorBefore: string | null = null;
  let cursorAfter: string | null = null;
  let fate: ReturnType<typeof delivery_fate_json> | null = null;
  const live: string[] = [];
  let events: ReturnType<ConversationStore['read_events_after']> = [];
  let lifecycle: ReturnType<ConversationStore['lifecycle_facts']> = [];
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    const { create_claude_backend_child_factory } =
      await import('../../backends/claude_agent_sdk/adapter.ts');
    const { claude_sdk_client } = await import('../../backends/claude_agent_sdk/query.ts');
    const clientFactory: ClaudeSdkClientFactory = (options) => {
      for (const name of Object.keys(options.env ?? {})) if (name.startsWith('PLAN_')) denied();
      const client = claude_sdk_client({
        ...options,
        tools: [],
        settingSources: [],
        strictMcpConfig: true,
        mcpServers: {},
        skills: [],
        plugins: [],
        hooks: {},
        agents: {},
        permissionMode: 'default',
        allowDangerouslySkipPermissions: false,
        settings: { disableAllHooks: true },
        includeHookEvents: true,
        canUseTool: async () => {
          prohibit('permission_callback');
          return { behavior: 'deny', message: 'Unit 18 manual proof denies every tool' };
        },
      });
      const receive = client.receive_messages.bind(client);
      client.receive_messages = async function* () {
        for await (const message of receive()) {
          nativeMessages.push(native_shape(message));
          if (message.type === 'system' && message.subtype === 'init') {
            initObserved = true;
            initToolsEmpty = Array.isArray(message.tools) && message.tools.length === 0;
            nativeModel = typeof message.model === 'string' ? message.model : null;
            haikuConfirmed =
              nativeModel?.toLowerCase() === 'haiku' ||
              /^claude-haiku-\d[0-9a-z._-]*(?:\[1m\])?$/i.test(nativeModel ?? '');
            if (!haikuConfirmed) {
              prohibit('model_mismatch');
              throw new Error('Native initialization did not confirm Haiku');
            }
            if (!initToolsEmpty) {
              prohibit('initialization_tools');
              throw new Error('Native initialization did not report empty tools');
            }
            if (Array.isArray(message.mcp_servers) && message.mcp_servers.length !== 0) {
              prohibit('initialization_mcp_servers');
              throw new Error('Unexpected native MCP server');
            }
          }
          if (
            message.type === 'system' &&
            ['hook_started', 'hook_progress', 'hook_response'].includes(String(message.subtype))
          ) {
            prohibit('hook_event');
            throw new Error('Unexpected hook event');
          }
          const body = record(message.message);
          const blocks = Array.isArray(body?.content) ? body.content.map(record) : [];
          if (
            message.type === 'assistant' &&
            blocks.some((block) => block?.type === 'tool_use' || block?.type === 'server_tool_use')
          ) {
            prohibit('assistant_tool');
            throw new Error('Unexpected native tool call');
          }
          if (
            promptStarted &&
            message.type === 'assistant' &&
            message.parent_tool_use_id == null &&
            body?.model !== '<synthetic>'
          ) {
            nativeText.push(
              blocks
                .filter((block) => block?.type === 'text')
                .map((block) => (typeof block?.text === 'string' ? block.text : ''))
                .join(''),
            );
          }
          if (message.type === 'result' && message.is_error)
            resultError =
              message.api_error_status === 401
                ? 'authentication'
                : failure_kind(
                    [message.result, ...(Array.isArray(message.errors) ? message.errors : [])]
                      .filter((value) => typeof value === 'string')
                      .join(' '),
                  );
          yield message;
        }
      };
      const query = client.query.bind(client);
      client.query = async (prompt) => {
        if (typeof prompt === 'string')
          throw new Error('The manual proof expects one UUID-stamped ordinary prompt');
        const messages = prompt;
        async function* observed(): AsyncGenerator<SDKUserMessage> {
          for await (const message of messages) {
            const content = message.message.content;
            const text =
              typeof content === 'string'
                ? content
                : content
                    .filter((block) => block.type === 'text')
                    .map((block) => ('text' in block ? block.text : ''))
                    .join('');
            outgoing.push({ uuid: message.uuid ?? null, text });
            if (outgoing.length !== 1 || !message.uuid)
              throw new Error('Manual proof permits exactly one UUID-stamped prompt');
            promptStarted = true;
            yield message;
          }
        }
        await query(observed());
      };
      const disconnect = client.disconnect.bind(client);
      const cleanup = { completed: false, failure_class: null as string | null };
      disconnected.push(cleanup);
      client.disconnect = async () => {
        try {
          await disconnect();
          cleanup.completed = true;
        } catch (error) {
          cleanup.failure_class = error instanceof Error ? error.constructor.name : 'unknown';
          throw error;
        }
      };
      return client;
    };
    database = createDatabase(spec.database);
    database.write((tx) =>
      tx.run(
        'INSERT INTO conversation_runtime_epoch VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch',
        ['unit18-manual-runtime'],
      ),
    );
    store = new ConversationStore(database);
    const existing = store.read_conversation(spec.conversation_id);
    if (spec.mode === 'fresh' && existing !== null)
      throw new Error('Fresh proof refuses an existing conversation');
    if (
      spec.mode === 'resume' &&
      (existing === null ||
        existing.vendor_session_cursor !== spec.expected_cursor ||
        existing.backend_key !== 'claude' ||
        existing.model !== 'haiku' ||
        existing.reasoning_effort !== 'low' ||
        existing.identity_environment_variables.length !== 0)
    )
      throw new Error('Resume does not match its exact allowlisted Python core record');
    cursorBefore = existing?.vendor_session_cursor ?? null;
    beforeSequence = existing?.latest_sequence ?? 0;
    beforeTurn = Math.max(
      0,
      ...store.lifecycle_facts(spec.conversation_id).map((fact) => fact.turn_number),
    );
    const overrides: [string, string][] = [];
    if (spec.claude_home !== undefined) overrides.push(['CLAUDE_CONFIG_DIR', spec.claude_home]);
    if (spec.scripted) {
      overrides.push(['UNIT18_PART3_SCRIPTED', '1']);
      if (spec.trace) overrides.push(['UNIT18_TRACE', spec.trace]);
    }
    const factory = create_claude_backend_child_factory(
      { claude_executable: spec.executable, environment_overrides: overrides },
      { client_factory: clientFactory },
    );
    const unsupported = () => {
      throw new Error('The manual proof permits only Claude');
    };
    core = new SqliteProcessConversationSystem({
      store,
      backend_child_factories: { claude: factory, codex: unsupported, hermes: unsupported },
      message_files: new ConversationMessageFiles(spec.database),
      publish_frame: (id, frame) => {
        if (id === spec.conversation_id && promptStarted && frame instanceof AgentMessageDeltaFrame)
          live.push(frame.text_delta);
      },
    });
    if (spec.mode === 'fresh')
      await core.start_conversation(
        new ConversationStartRequest({
          conversation_id: spec.conversation_id,
          backend_key: 'claude',
          model: 'haiku',
          reasoning_effort: 'low',
          workspace_folder: spec.workspace,
          workspace_path_policy: 'absolute',
          role_materials: null,
        }),
      );
    const exercise = async () => {
      fate = delivery_fate_json(
        await core!.send(spec.conversation_id, text_message_content(spec.prompt), {
          sender_label: 'Owner',
          sender_message_id: `${spec.mode}-manual-proof`,
        }),
      );
      if (fate.fate !== 'started') throw new Error('The one manual prompt did not start');
      while (true) {
        const current = store!.read_events_after(spec.conversation_id, beforeSequence);
        if (
          current.some((event) => event.kind === 'turn_ended') &&
          !(await core!.is_running(spec.conversation_id))
        )
          break;
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      await core!.wait_until_quiescent();
    };
    await Promise.race([
      exercise(),
      unexpectedActivity,
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(() => reject(new Error('Manual turn deadline exceeded')), 120_000);
      }),
    ]);
  } catch (error) {
    stopReason = launchBudgetViolation
      ? 'launch_budget'
      : unexpected
        ? unexpected === 'model_mismatch'
          ? 'model_mismatch'
          : 'unexpected_tool_or_hook'
        : (resultError ?? failure_kind(error instanceof Error ? error.message : ''));
    errorClass = error instanceof Error ? error.constructor.name : 'unknown';
  } finally {
    clearTimeout(timeout);
    if (core !== null) {
      try {
        const shutdown = await bounded(core.shutdown(), 20);
        shutdownCompleted = !shutdown.timed_out;
        if (shutdown.timed_out) shutdownFailure = 'CoreShutdownDeadlineExceeded';
      } catch (error) {
        shutdownFailure = error instanceof Error ? error.constructor.name : 'unknown';
      }
    }
    fallbackRequired =
      !shutdownCompleted ||
      disconnected.some((item) => !item.completed) ||
      launches.some((item) => !item.closed);
    if (fallbackRequired) {
      stopReason ??= 'cleanup_failure';
      await Promise.all(
        handles.map(async (child, index) => {
          const item = launches[index]!;
          if (item.closed) return;
          const evidence = {
            pid: child.pid ?? null,
            term_requested: false,
            kill_requested: false,
            closed: false,
            failure_class: null as string | null,
          };
          fallbackCleanup.push(evidence);
          evidence.term_requested = true;
          try {
            child.kill('SIGTERM');
          } catch (error) {
            evidence.failure_class = error instanceof Error ? error.constructor.name : 'unknown';
          }
          if (!(await waitClosed(child, item))) {
            evidence.kill_requested = true;
            try {
              child.kill('SIGKILL');
            } catch (error) {
              evidence.failure_class = error instanceof Error ? error.constructor.name : 'unknown';
            }
            await waitClosed(child, item);
          }
          evidence.closed = item.closed;
        }),
      );
    }
    try {
      if (store !== null) {
        events = store.read_events_after(spec.conversation_id, beforeSequence);
        lifecycle = store
          .lifecycle_facts(spec.conversation_id)
          .filter((fact) => fact.turn_number > beforeTurn);
        cursorAfter = store.read_conversation(spec.conversation_id)?.vendor_session_cursor ?? null;
      }
    } finally {
      try {
        database?.close();
      } finally {
        unregister();
        Object.assign(childProcess, original);
        syncBuiltinESMExports();
      }
    }
  }
  const ending = events.findLast((event) => event.kind === 'turn_ended');
  const endingValue = ending && 'ending' in ending.payload ? ending.payload.ending : null;
  if (!stopReason && resultError) stopReason = resultError;
  if (!stopReason && unexpected)
    stopReason = unexpected === 'model_mismatch' ? 'model_mismatch' : 'unexpected_tool_or_hook';
  const cursorMatches =
    cursorAfter !== null && (spec.mode === 'fresh' || cursorAfter === spec.expected_cursor);
  const completed = endingValue === 'completed';
  const liveText = live.join('');
  const completedNative = nativeText.join('');
  const durableReply = events
    .filter((event) => event.kind === 'agent_message')
    .map((event) => ('content' in event.payload ? message_content_text(event.payload.content) : ''))
    .join('');
  const cleanup = {
    core_shutdown: shutdownCompleted,
    failure_class: shutdownFailure,
    clients: disconnected,
    all_owned_handles_closed:
      launches.length === handles.length && launches.every((item) => item.closed),
    fallback_required: fallbackRequired,
    fallback: fallbackCleanup,
  };
  const environmentPreserved = isDeepStrictEqual(environmentBefore, { ...process.env });
  const success =
    stopReason === null &&
    completed &&
    cursorMatches &&
    initObserved &&
    initToolsEmpty &&
    haikuConfirmed &&
    liveText.length > 0 &&
    cleanup.core_shutdown &&
    cleanup.clients.every((item) => item.completed) &&
    cleanup.all_owned_handles_closed &&
    !fallbackRequired &&
    environmentPreserved;
  return {
    mode: spec.mode,
    conversation_id: spec.conversation_id,
    prompt: spec.prompt,
    delivery_fate: fate,
    live_text: liveText,
    reply: liveText || completedNative,
    reply_source: liveText ? 'new_live_text' : 'new_native_completed_text',
    completed_native_text: completedNative,
    durable_reply: durableReply,
    events: events.map((event) => ({
      sequence: event.sequence,
      kind: event.kind,
      ...(event.kind === 'turn_ended' && 'ending' in event.payload
        ? { ending: event.payload.ending }
        : {}),
    })),
    lifecycle,
    usage: events.filter((event) => event.kind === 'token_usage').map((event) => event.payload),
    cursor_before: cursorBefore,
    cursor_after: cursorAfter,
    cursor_matches: cursorMatches,
    model: nativeModel,
    native_haiku_confirmed: haikuConfirmed,
    reasoning_effort: 'low',
    reasoning_effort_source: 'request',
    native_init_tools_empty: initObserved && initToolsEmpty,
    native_messages: nativeMessages,
    raw_native_messages: rawNativeMessages,
    raw_malformed_lines: rawMalformedLines,
    native_visibility:
      'Owned child stdout before SDK parsing plus the public SDK iterator; only field shapes and current-prompt receipt facts are retained.',
    outgoing,
    launches,
    version_probes: launches.filter((item) => item.probe),
    cleanup,
    success,
    launch_budget: {
      version_probe_started: versionProbeStarted,
      cli_started: cliStarted,
      violated: launchBudgetViolation,
    },
    stop_reason: stopReason ?? (success ? null : 'proof_incomplete'),
    error_class: errorClass,
    environment_preserved: environmentPreserved,
    elapsed_seconds: (performance.now() - started) / 1000,
  };
}
