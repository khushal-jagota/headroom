// Gap: lower workspace and wire tests do not prove Project repository storage, home conversation
// workspace preparation from Project records, or remote MacRuns StartRun repository propagation.
// Reuses ticket_fixture and a temporary bare Git remote without launching a provider process.
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import {
  configure_conversation_start,
  start_ticket_conversation,
  worker_resolve,
} from '../runtime/conversation_start.ts';
import { createProject, readProject, updateProject } from '../projects/data/index.ts';
import { git } from '../ticket_workspaces/filesystem/shared.ts';
import { filesRoot } from '../files/logic/paths.ts';
import { ConversationStartRequest, type ConversationSystem } from '../conversation/contracts.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { MacPresenceService } from '../executor/vps_presence.ts';
import { SqliteCommandMailbox } from '../executor/vps_mailbox.ts';
import { MacRuns } from '../executor/vps_runs.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { create, fixture, fixture_path, now } from './ticket_fixture.ts';

async function bare_remote(root: string): Promise<string> {
  const remote = join(root, 'origin.git');
  const source = join(root, 'source');
  mkdirSync(remote, { recursive: true });
  mkdirSync(source, { recursive: true });
  for (const [cwd, args] of [
    [remote, ['init', '--bare']],
    [remote, ['symbolic-ref', 'HEAD', 'refs/heads/staging']],
    [source, ['init', '-b', 'staging']],
    [source, ['config', 'user.email', 'proof@example.test']],
    [source, ['config', 'user.name', 'Synthetic proof']],
  ] as const) {
    const result = await git(cwd, [...args]);
    expect(result.returncode, result.stderr).toBe(0);
  }
  writeFileSync(join(source, 'marker.txt'), 'from-project\n');
  for (const args of [
    ['add', '.'],
    ['commit', '-m', 'init'],
    ['remote', 'add', 'origin', remote],
    ['push', 'origin', 'staging'],
  ]) {
    const result = await git(source, args);
    expect(result.returncode, result.stderr).toBe(0);
  }
  return remote;
}

test('Project stores repository_url and default_branch with branch-requires-url validation', () => {
  const database = fixture();
  expect(() =>
    database.write((tx) =>
      createProject(tx, { name: 'Bad', summary: '', default_branch: 'main' }, now),
    ),
  ).toThrow('default_branch requires repository_url');

  const created = database.write((tx) =>
    createProject(
      tx,
      {
        name: 'Tracked',
        summary: '',
        repository_url: 'https://github.com/khushal-jagota/panels.git',
        default_branch: 'staging',
      },
      now,
    ),
  );
  expect(created.repository_url).toBe('https://github.com/khushal-jagota/panels.git');
  expect(created.default_branch).toBe('staging');

  const cleared = database.write((tx) =>
    updateProject(tx, created.id, { repository_url: null }, now + 1),
  );
  expect(cleared.repository_url).toBeNull();
  expect(cleared.default_branch).toBeNull();
});

test('Home start clones from Project repository_url regardless of folder_path and fails cleanly on bad remote', async () => {
  const database = fixture();
  const db_path = fixture_path(database);
  const root = join(dirname(db_path), 'repo-proof');
  const remote = await bare_remote(root);
  const non_git_folder = join(root, 'empty-folder');
  mkdirSync(non_git_folder, { recursive: true });
  configure_conversation_start(database, { db_path, workspace_root: root });

  const project = database.write((tx) =>
    createProject(
      tx,
      {
        name: 'ClonedProject',
        summary: '',
        folder_path: non_git_folder,
        repository_url: remote,
        default_branch: 'staging',
      },
      now,
    ),
  );
  const ticket = create(database, { project_id: project.id });
  const started: ConversationStartRequest[] = [];
  const fake_system = {
    async start_conversation(request: ConversationStartRequest) {
      started.push(request);
    },
    async kill() {},
  } as unknown as ConversationSystem;

  const values = worker_resolve(database, ticket);
  const linked = await start_ticket_conversation(fake_system, database, ticket, values, {
    conversation_id: 'conv_cloned',
    now,
  });
  expect(linked.made_here).toBe(true);
  expect(started).toHaveLength(1);
  const worktree = join(filesRoot(db_path, 'ticket'), ticket.id, 'worktree');
  expect(existsSync(join(worktree, 'marker.txt'))).toBe(true);

  const broken = database.write((tx) =>
    createProject(
      tx,
      {
        name: 'BrokenProject',
        summary: '',
        repository_url: join(root, 'missing.git'),
        default_branch: 'main',
      },
      now,
    ),
  );
  const broken_ticket = create(database, { project_id: broken.id });
  await expect(
    start_ticket_conversation(
      fake_system,
      database,
      broken_ticket,
      worker_resolve(database, broken_ticket),
      { conversation_id: 'conv_broken', now },
    ),
  ).rejects.toThrow('Cannot prepare the Ticket workspace');
  expect(existsSync(join(filesRoot(db_path, 'ticket'), broken_ticket.id, 'worktree'))).toBe(false);
  expect(started).toHaveLength(1);
});

test('MacRuns populates StartRun.workspace_repository from the Ticket effective Project', async () => {
  const database = fixture();
  const db_path = fixture_path(database);
  configure_conversation_start(database, { db_path, workspace_root: dirname(db_path) });
  const project = database.write((tx) =>
    createProject(
      tx,
      {
        name: 'RemoteRepo',
        summary: '',
        repository_url: 'https://github.com/khushal-jagota/Wren.git',
        default_branch: 'main',
      },
      now,
    ),
  );
  const manager_id = 'si_remote_mgr';
  database.write((tx) => {
    tx.run(
      "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,created_at,updated_at) VALUES (?,?,?,'codex','gpt-6.1-sol',?,?)",
      [manager_id, 'Manager', project.id, now, now],
    );
  });
  const ticket = create(database, {
    locale: 'macos',
    machine_id: ORIGINAL_MAC_MACHINE_ID,
    manager_id,
    project_id: null,
  });
  expect(database.read((sql) => readProject(sql, project.id)).repository_url).toBe(
    'https://github.com/khushal-jagota/Wren.git',
  );

  const mailbox = new SqliteCommandMailbox(database, { machine_id: ORIGINAL_MAC_MACHINE_ID });
  const presence = new MacPresenceService(database, mailbox, {
    changes: new ChangeSignal(),
    machine_id: ORIGINAL_MAC_MACHINE_ID,
    application_revision: 'synthetic',
  });
  const runs = new MacRuns(database, presence, mailbox);
  const incarnation = '11111111-1111-4111-8111-111111111111';
  await presence.accept_lease({
    identity: {
      protocol_version: PROTOCOL_VERSION,
      application_revision: 'synthetic',
      spool_format_version: SPOOL_FORMAT_VERSION,
      executor_incarnation: incarnation,
      connection_generation: 1,
    },
    process_id: 123,
    mac_wall_clock_ms: Date.now(),
    queue_depth: 0,
    oldest_item_age_ms: null,
    waiting_conversations: [],
    waiting_conversations_truncated: false,
    working_turns: [],
    admission_stopped: false,
    cleanup_uncertainties: [],
    driver_kinds: ['codex'],
  });
  try {
    const values = worker_resolve(database, ticket);
    const start = new ConversationStartRequest({
      conversation_id: 'conv_remote_repo',
      backend_key: values.backend_key,
      model: values.model,
      reasoning_effort: values.reasoning_effort,
      role_materials: values.role_materials,
      workspace_folder: values.workspace_folder,
      workspace_path_policy: values.workspace_path_policy,
      access: values.access,
    });
    database.write((tx) => {
      tx.run(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,workspace_path_policy,access,created_at) VALUES('conv_remote_repo','codex','model','/','executor_default','full',?)",
        [now],
      );
      tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', ['conv_remote_repo', ticket.id]);
    });

    const child = runs.create({
      resolved_start: {
        conversation_id: start.conversation_id,
        backend_key: start.backend_key!,
        model: start.model,
        reasoning_effort: start.reasoning_effort,
        role_materials: start.role_materials,
        workspace_folder: start.workspace_folder!,
        workspace_path_policy: start.workspace_path_policy,
        access: start.access!,
      },
      event_sink: { async report() {} },
      message_files: null as never,
    });
    await child.start(
      {
        conversation_id: start.conversation_id,
        backend_key: start.backend_key!,
        model: start.model,
        reasoning_effort: start.reasoning_effort,
        role_materials: start.role_materials,
        workspace_folder: start.workspace_folder!,
        workspace_path_policy: start.workspace_path_policy,
        access: start.access!,
      },
      { vendor_session_cursor: null },
    );
    const command = database.read((sql) =>
      sql.get<{ command_json: string }>(
        "SELECT command_json FROM executor_commands WHERE conversation_id='conv_remote_repo' AND operation='start'",
      ),
    );
    expect(command).toBeDefined();
    const parsed = JSON.parse(command!.command_json) as {
      child_command: {
        arguments: {
          workspace_repository?: { repository_url: string | null; default_branch: string | null };
        };
      };
    };
    expect(parsed.child_command.arguments.workspace_repository).toEqual({
      repository_url: 'https://github.com/khushal-jagota/Wren.git',
      default_branch: 'main',
    });
  } finally {
    const deadline = performance.now() / 1000 + 2;
    await runs.stop({ deadline });
    await presence.stop({ deadline });
    await mailbox.stop({ deadline });
  }
});
