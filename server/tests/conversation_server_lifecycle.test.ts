/** Startup and shutdown own one record runtime and retain its lease through cleanup. */
import { dirname, join, resolve } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import type { TestCompositionContext } from '../core/start.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { ManagerWakeLoop } from '../manager_wakes/runtime.ts';
import { ScriptedBackendRegistry } from '../conversation/testing/scripted_registry.ts';
import {
  BackendPermissionAsk,
  PermissionAskRaised,
  BackgroundTaskStarted,
  BackgroundTaskEnded,
} from '../conversation/backends/contracts.ts';
import { PermissionAskOption, UserInputAnswer } from '../conversation/events.ts';
import type { CurrentViewSnapshot } from '../../web/src/lib/navigation/contracts.ts';
import { ConversationViewRebuilder } from '../conversation/views/data/rebuild.ts';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { CLEANUP_INTERVAL_SECONDS } from '../conversation/video_uploads/configuration.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
function setup() {
  const database = fixture();
  const path = database.read((sql) =>
    sql.get<{ file: string }>("SELECT file FROM pragma_database_list WHERE name='main'"),
  )!.file;
  const root = dirname(path);
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: path,
    PLAN_WORKSPACE_ROOT: root,
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'control.sock'),
    PLAN_HERMES_HOME: join(root, 'hermes'),
    PLAN_APP_ROOT: resolve(import.meta.dirname, '../..'),
    PLAN_FAKE_NOW: '2026-11-11T10:00:00+00:00',
  }))
    vi.stubEnv(key, value);
  vi.stubEnv('PLAN_SERVER_LISTENER_FD', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_ROOT', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_DATABASE', undefined);
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'old-view',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'old-runtime')");
    tx.run(`INSERT INTO conversations
      (conversation_id,backend_key,model,workspace_folder,access,created_at)
      VALUES('retained','codex','fixture','/synthetic','full',0)`);
  });
  return { database, path };
}

// Distinct gap: protocol tests do not prove the process composition shares the
// record's sole reconciliation/epoch or leaves normal sends explicitly unavailable.
test('normal staged server serves the reconciled record through real publication without a fabricated provider', async () => {
  const f = setup();
  let context: TestCompositionContext | undefined;
  let epochBeforePublication: string | undefined;
  const server = await startServer({
    beforePublication(database) {
      epochBeforePublication = database.read((sql) =>
        sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch'),
      )!.epoch;
    },
    testAreas(value) {
      context = value;
      return [];
    },
  });
  try {
    expect(context?.conversationSystem).toBeUndefined();
    expect(epochBeforePublication).not.toBe('old-runtime');
    expect(
      f.database.read((sql) =>
        sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch'),
      )?.epoch,
    ).toBe(epochBeforePublication);
    const response = await fetch(server.address + '/api/conversation/conversations/retained/view');
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ page: { conversation_id: 'retained' } });
    expect(() => acquireRuntimeOwnership(f.path)).toThrow('ConversationRuntimeAlreadyOwned');
  } finally {
    await server.close();
  }
  const replacement = acquireRuntimeOwnership(f.path);
  replacement.close();
});

// Distinct gap: an initialization exception must stop the rebuilder/drain the
// existing record and release the actual borrowed ownership, without an HTTP app.
test('partial startup failure drains the record and releases the exact lease', async () => {
  const f = setup();
  await expect(
    startServer({
      beforePublication() {
        throw new Error('bootstrap proof failure');
      },
    }),
  ).rejects.toThrow('bootstrap proof failure');
  const replacement = acquireRuntimeOwnership(f.path);
  replacement.close();
  expect(
    f.database.read((sql) => sql.get('SELECT epoch FROM conversation_runtime_epoch')),
  ).toBeDefined();
});

// Distinct gap: isolated publication tests use a synthetic runtime reader. This
// uses the ordinary Chief send route, shared core/record/tail, scripted process,
// real navigation projection, and actual server shutdown with pending HTTP work.
test('ordinary sends publish real core status and shutdown fences a blocked native receipt before lease release', async () => {
  const f = setup();
  vi.stubEnv('PLAN_TEST_MODE', '0');
  vi.stubEnv('PLAN_DISPATCH_ENABLED', 'true');
  const registry = new ScriptedBackendRegistry();
  const wakePoll = vi.spyOn(ManagerWakeLoop.prototype, 'poll_once');
  const wakeStop = vi.spyOn(ManagerWakeLoop.prototype, 'stop');
  const intervals = vi.spyOn(globalThis, 'setInterval');
  const clear_interval = vi.spyOn(globalThis, 'clearInterval');
  const video_cleanup = vi.spyOn(VideoUploadService.prototype, 'recover_and_cleanup');
  let context!: TestCompositionContext;
  // Existing actual-core host also proves replay admission without counting it as busy.
  f.database.write((tx) => {
    for (const id of ['replay-first', 'replay-second']) {
      tx.run(`INSERT INTO conversations
        (conversation_id,backend_key,model,workspace_folder,access,created_at,latest_sequence)
        VALUES(?,'codex','fixture','/synthetic','full',0,1)`, [id]);
      tx.run(`INSERT INTO conversation_events VALUES(?,1,'command_reply','{"reply_text":"Replay fixture"}',0)`, [id]);
    }
  });
  let ready = false;
  let replay_checks = 0;
  const rebuild_start = ConversationViewRebuilder.prototype.start;
  vi.spyOn(ConversationViewRebuilder.prototype, 'start').mockImplementation(function(this: ConversationViewRebuilder, database, admission_closed) {
    expect(ready).toBe(true);
    expect(context.core).toBeDefined();
    context.publication.current_read();
    expect(context.core!.workspace_admission.active).toBe(false);
    rebuild_start.call(this, database, () => {
      replay_checks++;
      if (replay_checks === 2) context.core!.workspace_admission.seal();
      return admission_closed!();
    });
  });
  const server = await startServer({
    // The explicit area loop shares this core, clock, and lease through shutdown.
    areaLoops: { managerWakes: true },
    createConversationCore(value) {
      return new SqliteProcessConversationSystem({
        store: value.record.store,
        backend_child_factories: registry.factories,
        message_files: new ConversationMessageFiles(value.config.db_path),
        unix_time_now: () => value.clock.nowUnix(),
        require_runtime_ownership: value.require_runtime_ownership,
        require_signal_current: () => value.publication.current_read(),
        save_attempt: value.publication.save_attempt,
        on_shutdown: value.revoke_signal,
        publish_event: (event) => value.record.live_tail.publish_event(event),
        publish_frame: (id, frame) => value.record.live_tail.publish_frame(id, frame),
        publish_status_changed: (id) => value.record.live_tail.views.publish_status_changed(id),
        emit_change: () => value.changes.emit(),
      });
    },
    testAreas(value) {
      context = value;
      return [{ prefix: '', routes: async (app) => {
        app.addHook('onReady', async () => { ready = true; });
      } }];
    },
  });
  const post = (text: string) =>
    fetch(server.address + '/api/chief/conversation/send', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        content: [{ piece: 'text', text }],
        sender_label: 'Owner',
        sender_message_id: text,
      }),
    });
  try {
    await vi.waitFor(() => expect(replay_checks).toBe(2));
    expect(f.database.read((sql) => sql.get('SELECT through_sequence FROM conversation_view_state WHERE conversation_id=?', ['replay-first']))).toEqual({ through_sequence: 1 });
    expect(f.database.read((sql) => sql.get('SELECT 1 FROM conversation_view_state WHERE conversation_id=?', ['replay-second']))).toBeUndefined();
    context.core!.workspace_admission.reopen();
    const cleanup_timer = intervals.mock.calls.map((args, index) => ({ args, timer: intervals.mock.results[index].value }))
      .filter(({ args }) => args[1] === CLEANUP_INTERVAL_SECONDS * 1000);
    expect(cleanup_timer).toHaveLength(1);
    const tick_cleanup = cleanup_timer[0].args[0] as () => void;
    const cleanup_calls = video_cleanup.mock.calls.length;
    const cleanup_error = new Error('Synthetic transient cleanup failure');
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    video_cleanup.mockImplementationOnce(() => { throw cleanup_error; });
    tick_cleanup();
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(errors).toHaveBeenCalledWith('Video upload cleanup failed', cleanup_error);
    expect(clear_interval).not.toHaveBeenCalledWith(cleanup_timer[0].timer);
    tick_cleanup();
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(video_cleanup).toHaveBeenCalledTimes(cleanup_calls + 2);
    errors.mockRestore();
    await vi.waitFor(() => expect(wakePoll).toHaveBeenCalled());
    await expect(wakePoll.mock.results[0].value).resolves.toEqual([]);
    const sent = await post('work');
    expect(sent.status).toBe(200);
    const delivery = (await sent.json()) as { conversation_id: string; fate: string };
    expect(delivery.fate).toBe('started');
    const id = delivery.conversation_id;
    const child = registry.child(id);
    const settle_backend_events = async () => {
      await child.flush_events();
      await context.core!.wait_until_quiescent();
    };
    const view = encodeURIComponent(
      JSON.stringify({
        projection_version: 7,
        scope_generation: 1,
        locale: 'en-US',
        selected: { kind: 'chief', id: 'chief' },
        selected_conversation_id: null,
        review_conversation_ids: [],
        review_skipped_keys: [],
        review_priority_keys: [],
        windows: [],
      }),
    );
    const attention = async () => {
      const response = await fetch(server.address + `/api/navigation/view/snapshot?view=${view}`);
      expect(response.status).toBe(200);
      const snapshot = (await response.json()) as CurrentViewSnapshot;
      const record = snapshot.content.records.find(
        (item) => item.kind === 'conversation' && item.id === id,
      );
      if (record?.kind !== 'conversation')
        throw new Error('active conversation is absent from navigation');
      return record.value.attention.current;
    };
    expect((await attention()).signal.working).toBe(true);
    await child.emit(
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id: 'permission',
          title: 'Permission',
          detail: null,
          options: [
            new PermissionAskOption({
              option_id: 'allow',
              label: 'Allow',
              option_kind: 'allow_once',
            }),
          ],
        }),
      }),
    );
    await settle_backend_events();
    await child.request_questions();
    await settle_backend_events();
    expect((await attention()).awaiting_answer).toBe(true);
    const turn = context.core!.runtime_turn(id)!;
    const [permission] = turn.pending_permission_ask_ids;
    const [question] = turn.pending_user_input_questions.keys();
    expect(await context.core!.answer_permission_ask(id, permission, 'allow')).toBe('applied');
    expect((await attention()).awaiting_answer).toBe(true);
    expect(
      await context.core!.answer_user_input(id, question, [
        new UserInputAnswer({ question_id: 'scope', answers: ['Composer'] }),
        new UserInputAnswer({ question_id: 'proof', answers: ['Browser test'] }),
      ]),
    ).toBe('applied');
    expect((await attention()).awaiting_answer).toBe(false);
    await child.emit(new BackgroundTaskStarted({ task_id: 'task', task_description: 'Task' }));
    await settle_backend_events();
    await child.finish();
    await settle_backend_events();
    expect(context.core!.runtime_turn(id)).toBeNull();
    expect((await attention()).signal.working).toBe(true);
    await child.emit(new BackgroundTaskEnded({ task_id: 'task', task_status: 'completed' }));
    await settle_backend_events();
    expect((await attention()).signal.working).toBe(false);
    expect((await post('fail')).status).toBe(200);
    await child.finish({ ending: 'failed', error_summary: 'Synthetic failure' });
    await settle_backend_events();
    expect((await attention()).last_turn_failed).toBe(true);
    const through = context.record.store.read_conversation(id)!.latest_sequence;
    // The acknowledgment's domain policy belongs elsewhere. Exercise its actual
    // source transaction and producer trigger without a fabricated route.
    context.record.database.write((tx) =>
      tx.run('INSERT INTO conversation_error_acknowledgements VALUES(?,?)', [id, through]),
    );
    expect((await attention()).last_turn_failed).toBe(false);
    await child.configure('write_prompt', { barrier: 'shutdown' });
    const pending = post('shutdown').then(
      () => null,
      () => null,
    );
    await child.wait_for_barrier('shutdown');
    await server.close();
    await pending;
    expect(wakeStop).toHaveBeenCalledOnce();
    for (const result of wakePoll.mock.results) await expect(result.value).resolves.toEqual([]);
    const replacement = acquireRuntimeOwnership(f.path);
    replacement.close();
  } finally {
    await server.close();
    await registry.close();
  }
});
