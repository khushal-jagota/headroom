/** Shared scripted Codex child behind the actual TS conversation core and records. */
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { createDatabase } from '../../../core/database/index.ts';
import { ConversationStore } from '../../record/data/store.ts';
import { SqliteProcessConversationSystem } from '../../system.ts';
import { ConversationMessageFiles } from '../../record/message_files.ts';
import { ConversationStartRequest, ConversationRoleMaterials } from '../../contracts.ts';
import { AgentMessageDeltaFrame, UserInputAnswer } from '../../events.ts';
import { text_message_content } from '../../message_content.ts';
import { delivery_fate_json } from '../../http.ts';
import { DurableCodexLoginRejection } from '../../backend_state.ts';
import { CodexAppServerClient } from '../../backends/codex_app_server/client.ts';
import { read_stored_codex_login } from '../../backends/codex_app_server/login.ts';

export interface AdapterParitySpec {
  database: string;
  workspace: string;
  executable: string;
  trace: string;
}
const tables = [
  'conversations',
  'conversation_events',
  'conversation_lifecycle_facts',
  'conversation_view_state',
  'conversation_view_rows',
  'conversation_view_turns',
  'conversation_view_groups',
  'conversation_view_lookups',
  'codex_login_rejection',
];
const rowOrders: Readonly<Record<string, string>> = {
  conversation_lifecycle_facts: 'conversation_id,turn_number,kind,operation,identity',
  conversation_view_lookups: 'conversation_id,lookup_kind,source_sequence,lookup_id',
};
export async function run_adapter_parity(spec: AdapterParitySpec): Promise<unknown> {
  const original = {
    spawn: childProcess.spawn,
    spawnSync: childProcess.spawnSync,
    exec: childProcess.exec,
    execSync: childProcess.execSync,
    execFile: childProcess.execFile,
    execFileSync: childProcess.execFileSync,
    fork: childProcess.fork,
  };
  const forbidden = (): never => {
    throw new Error('Only the owned scripted Codex child can start');
  };
  Object.assign(childProcess, {
    spawn(executable: string, args: string[], options: Parameters<typeof childProcess.spawn>[2]) {
      if (
        executable !== spec.executable ||
        args.length !== 1 ||
        !['app-server', '--version'].includes(args[0]!)
      )
        forbidden();
      return original.spawn(executable, args, options);
    },
    spawnSync: forbidden,
    exec: forbidden,
    execSync: forbidden,
    execFile: forbidden,
    execFileSync: forbidden,
    fork: forbidden,
  });
  syncBuiltinESMExports();
  const { create_codex_backend_child_factory } =
    await import('../../backends/codex_app_server/adapter.ts');
  const database = createDatabase(spec.database);
  database.write((tx) =>
    tx.run(
      'INSERT INTO conversation_runtime_epoch VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch',
      ['unit17-runtime'],
    ),
  );
  const store = new ConversationStore(database, { integer_now: () => 100 });
  const rejection = new DurableCodexLoginRejection(database, () => new Date(100000));
  const backlog_order: string[] = [];
  const base_factory = create_codex_backend_child_factory(
    {
      argv: [spec.executable, 'app-server'],
      environment_overrides: [
        ['UNIT17_TRACE', spec.trace],
        ['UNIT17_CORE_PROOF', '1'],
      ],
    },
    {
      remembered_login_rejection: rejection,
      client_factory: (options) =>
        new CodexAppServerClient({
          ...options,
          handler: {
            on_server_request: (...args) => options.handler.on_server_request(...args),
            on_child_ended: () => options.handler.on_child_ended(),
            async on_notification(method, notification) {
              if (method === 'skills/changed') {
                backlog_order.push('handler-start');
                await new Promise((resolve) => setTimeout(resolve, 20));
                backlog_order.push('handler-end');
              }
              await options.handler.on_notification(method, notification);
            },
          },
        }),
    },
  );
  const factory: typeof base_factory = (options) => {
    const offset = existsSync(spec.trace)
      ? readFileSync(spec.trace, 'utf8').trim().split('\n').length
      : 0;
    const child = base_factory(options);
    const prompt = child.write_prompt.bind(child);
    let first = true;
    child.write_prompt = async (...args) => {
      if (first) {
        first = false;
        const deadline = performance.now() + 10000;
        while (
          !existsSync(spec.trace) ||
          !readFileSync(spec.trace, 'utf8')
            .trim()
            .split('\n')
            .slice(offset)
            .some((line) => (JSON.parse(line) as { method?: string }).method === 'app/read')
        ) {
          if (performance.now() > deadline)
            throw new Error('Timed out: driver discovery-before-first-input boundary');
          await new Promise((resolve) => setTimeout(resolve, 5));
        }
      }
      return prompt(...args);
    };
    return child;
  };
  const live_text: { type: string; text_delta: string }[] = [];
  const core = new SqliteProcessConversationSystem({
    store,
    backend_child_factories: { codex: factory, claude: factory, hermes: factory },
    message_files: new ConversationMessageFiles(spec.database),
    unix_time_now: () => 100,
    publish_frame: (_id, frame) => {
      if (frame instanceof AgentMessageDeltaFrame)
        live_text.push({ type: 'agent_message_delta', text_delta: frame.text_delta });
    },
  });
  const id = 'unit17-codex';
  const checkpoints: unknown[] = [];
  let sender = 0;
  const send = async (scenario: string, mode: 'queue' | 'steer' = 'queue', native = false) =>
    delivery_fate_json(
      await core.send(
        id,
        text_message_content(
          native
            ? scenario
            : scenario === 'malformed'
              ? '@synthetic-calendar [CASE:malformed]'
              : `[CASE:${scenario}]`,
        ),
        {
          sender_label: 'Owner',
          sender_message_id: `sender-${++sender}`,
          mode,
        },
      ),
    );
  const wait = async (condition: () => boolean | Promise<boolean>, description: string) => {
    const deadline = performance.now() + 10000;
    while (!(await condition())) {
      if (performance.now() > deadline) throw new Error(`Timed out: ${description}`);
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    await core.wait_until_quiescent();
  };
  const ended = async (count: number) =>
    wait(async () => {
      const actual = database.read(
        (sql) =>
          sql.get<{ count: number }>(
            "SELECT count(*) AS count FROM conversation_events WHERE kind='turn_ended'",
          )!.count,
      );
      return actual >= count && !(await core.is_running(id));
    }, `turn ${count} ending`);
  const capture = async (name: string, fates: unknown[], conversation_id = id) => {
    await core.wait_until_quiescent();
    const rows = database.read((sql) =>
      Object.fromEntries(
        tables.map((table) => [
          table,
          sql.all<Record<string, unknown>>(
            `SELECT * FROM ${table} ORDER BY ${rowOrders[table] ?? '1,2'}`,
          ),
        ]),
      ),
    );
    const login = read_stored_codex_login();
    checkpoints.push({
      name,
      fates,
      rows,
      running: await core.is_running(conversation_id),
      login: { generation: login.generation, rejected: rejection.applies_to(login.generation) },
    });
  };
  try {
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: id,
        backend_key: 'codex',
        model: 'gpt-6-luna',
        reasoning_effort: 'low',
        workspace_folder: spec.workspace,
        workspace_path_policy: 'absolute',
        role_materials: new ConversationRoleMaterials({
          role_text: 'Synthetic owner',
          identity_environment_variables: [['PLAN_ACTOR', 'owner']],
        }),
      }),
    );
    let count = 0;
    const plain = await send('plain');
    const catalog_ready = () =>
      database.read((sql) => {
        const row = sql.get<{ composer_catalog: string }>(
          'SELECT composer_catalog FROM conversations WHERE conversation_id=?',
          [id],
        );
        const entries = row?.composer_catalog
          ? (JSON.parse(row.composer_catalog) as { insertion_text: string }[])
          : [];
        const tokens = new Set(entries.map((entry) => entry.insertion_text));
        return [
          '$synthetic-skill ',
          '@synthetic-tools@synthetic-market ',
          '@synthetic-calendar ',
        ].every((token) => tokens.has(token));
      });
    await wait(catalog_ready, 'populated catalog sources and app metadata');
    await ended(++count);
    await capture('plain', [plain]);
    const queueBase = await send('queue-base');
    const queued = await send('queued');
    const held = await core.held_prompts(id);
    if (held.length !== 1) throw new Error('Expected exactly one held message');
    const discarded = await core.discard_held_prompt(id, held[0]!.held_prompt_id);
    await core.interrupt(id);
    if (
      !(
        JSON.parse(readFileSync(`${spec.trace}.state.json`, 'utf8')) as {
          interrupt_settled: boolean;
        }
      ).interrupt_settled
    )
      throw new Error('Interrupt returned before terminal evidence');
    await ended(++count);
    await capture('queued-and-cancelled', [queueBase, queued, { discarded }]);
    const permission = await send('permission');
    await wait(() => core.has_pending_permission_ask(id), 'permission ask');
    const ask = [...(await core.pending_permission_ask_ids(id))][0]!;
    const permissionAnswer = await core.answer_permission_ask(id, ask, 'accept');
    if (permissionAnswer !== 'applied')
      throw new Error(`Permission answer refused: ${permissionAnswer}`);
    await ended(++count);
    await capture('permission', [permission, { answer_fate: permissionAnswer }]);
    const question = await send('question');
    await wait(() => core.has_pending_user_input(id), 'question');
    const questionId = [...(await core.pending_user_input_request_ids(id))][0]!;
    const questionAnswer = await core.answer_user_input(id, questionId, [
      new UserInputAnswer({ question_id: 'color', answers: ['Blue'] }),
    ]);
    if (questionAnswer !== 'applied') throw new Error(`Question answer refused: ${questionAnswer}`);
    await ended(++count);
    await capture('question', [question, { answer_fate: questionAnswer }]);
    const interrupt = await send('interrupt-base');
    await core.interrupt(id);
    if (
      !(
        JSON.parse(readFileSync(`${spec.trace}.state.json`, 'utf8')) as {
          interrupt_settled: boolean;
        }
      ).interrupt_settled
    )
      throw new Error('Interrupt returned before terminal evidence');
    await ended(++count);
    await capture('interrupt', [interrupt]);
    const steerBase = await send('steer-base');
    const steer = await send('steer', 'steer');
    await ended(++count);
    const refusedBase = await send('steer-race-base');
    const refusedSteer = await send('steer-refused', 'steer');
    count += 2;
    await ended(count);
    await capture('steer', [steerBase, steer, refusedBase, refusedSteer]);
    await core.kill(id);
    const resumed = await send('resume-success');
    await wait(catalog_ready, 'resumed child populated catalog');
    await ended(++count);
    await capture('successful-resume', [resumed]);
    for (const scenario of ['auth-failure', 'malformed', 'auth-refresh']) {
      const fate = await send(scenario);
      await ended(++count);
      await capture(scenario, [fate]);
    }
    for (const command of ['/compact', '/review', '/goal get']) {
      const fate = await send(command, 'queue', true);
      await ended(++count);
      await capture(command, [fate]);
    }
    const uncertain = await send('rejected-after-output');
    backlog_order.push('receipt');
    await capture('rejected-after-output', [uncertain]);
    await core.kill(id);
    const refused = await send('resume-must-fail');
    await capture('failed-resume', [refused]);
    const noReceipt = 'unit17-no-receipt';
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: noReceipt,
        backend_key: 'codex',
        model: 'gpt-6-luna',
        reasoning_effort: 'low',
        workspace_folder: spec.workspace,
        workspace_path_policy: 'absolute',
        role_materials: new ConversationRoleMaterials({
          role_text: 'Synthetic owner',
          identity_environment_variables: [['PLAN_ACTOR', 'owner']],
        }),
      }),
    );
    const missing = delivery_fate_json(
      await core.send(noReceipt, text_message_content('[CASE:exit-before-reply]'), {
        sender_label: 'Owner',
        sender_message_id: 'missing-receipt',
      }),
    );
    await capture('exit-before-reply', [missing], noReceipt);
  } finally {
    await core.shutdown();
    database.close();
    Object.assign(childProcess, original);
    syncBuiltinESMExports();
  }
  return {
    checkpoints,
    live_text,
    backlog_order,
    trace: readFileSync(spec.trace, 'utf8')
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line)),
    children: readFileSync(`${spec.trace}.children`, 'utf8')
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line)),
  };
}
