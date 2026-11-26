import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { run } from './main.ts';
import * as http from './http.ts';
import { WORKER_STATUS_LABEL } from '../tickets/logic/status_presentation.ts';

const SERVER = { PLAN_SERVER_URL: 'http://127.0.0.1:19020', PLAN_ACTOR: '' };
const complete_page = (count: number) => ({
  return_count: count,
  match_count: count,
  omitted_before: 0,
  omitted_after: 0,
  complete: true,
  next_offset: null,
});

// Risk: the list sends a removed filter or the wrong scope, or hides what it left out.
// Freezing the clock also proves the recap age boundary and the stage-changed marker.
test('ticket list sends scope, needs, and Manager kind and says what it did not show', async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-07-04T12:00:02Z'));
  const base = {
    id: 't_recap',
    stage: 'needs_implementation',
    title: 'Recap',
    ticket_status: 'agent_owned',
    agent_state: 'idle',
  };
  const request = vi.spyOn(http, 'send').mockResolvedValue({
    tickets: [
      {
        ...base,
        needs: 'agent_approval',
        ceiling_holder: { kind: 'chief', id: 'chief' },
        recap: 'Current\nrecap',
        recap_updated_at: Math.trunc(Date.now() / 1000),
        recap_stage_changed: true,
      },
      { ...base, needs: 'owner_approval', recap: 'Older recap', recap_updated_at: null },
      { ...base, needs: 'working', recap: ' ', recap_updated_at: null },
    ],
    page: { ...complete_page(3), not_shown: { backlog: 9, done: 104 } },
  });
  try {
    const output = await run(
      ['ticket', 'list', '--chief', '--needs', 'owner_approval', '--needs', 'working'],
      SERVER,
    );
    expect(output).toMatchObject({ exit_code: 0, stderr: '' });
    expect(request.mock.lastCall?.slice(1)).toEqual([
      'GET',
      '/api/tickets',
      {
        params: {
          detail: 'summary',
          scope: 'live',
          needs: ['owner_approval', 'working'],
          manager_kind: 'chief',
          limit: 30,
          offset: 0,
        },
      },
    ]);
    expect(output.stdout.split('\n').slice(0, 5)).toEqual([
      'Recap',
      '  Current recap (recap 0s old, stage changed since)',
      `  status: ${WORKER_STATUS_LABEL} | Worker: idle`,
      '  stage: needs_implementation | needs: Chief: approve',
      '  id: t_recap',
    ]);
    expect(output.stdout).toContain('Current recap (recap 0s old, stage changed since)');
    expect(output.stdout).toContain('Older recap (recap age unknown)');
    expect(output.stdout).toContain('\n  no recap');
    expect(output.stdout).toContain(
      '3 live Tickets.\nNot shown: 9 in backlog, 104 done. Add --backlog, --done, or --all.\n',
    );
    expect(output.stdout).not.toContain('Returned');
    await run(['ticket', 'list', '--done', '--manager', 'si_1'], SERVER);
    expect(request.mock.lastCall?.[3]?.params).toMatchObject({ scope: 'done', manager_id: 'si_1' });
    expect((await run(['ticket', 'list', '--done', '--all'], SERVER)).exit_code).toBe(1);
  } finally {
    request.mockRestore();
    vi.useRealTimers();
  }
});

// Scalar-only checks miss facade exception conversion and Python.get pagination behavior.
test('CLI validates missing native values and skips an absent optional page continuation', async () => {
  const request = vi.spyOn(http, 'send').mockResolvedValue({});
  try {
    const missing = await run(
      ['project', 'create', '--name', 'Missing wire values', '--json'],
      SERVER,
    );
    expect(missing).toMatchObject({ exit_code: 1, stdout: '' });
    expect(JSON.parse(missing.stderr).error).toMatchObject({
      code: 'validation',
      message: expect.stringMatching(/required/),
    });
    request.mockResolvedValue({
      datasets: [],
      page: { return_count: 0, match_count: 0, omitted_before: 0, omitted_after: 0 },
    });
    const paged = await run(['dataset', 'list'], SERVER);
    expect(paged).toMatchObject({ exit_code: 0, stderr: '' });
    expect(paged.stdout).toContain('Returned 0 of 0 matches.');
    expect(paged.stdout).not.toMatch(/Use --limit|undefined|\[object Object\]/);
    // Python dict.get never returns inherited values or approval functions.
    const names = ['__proto__', 'constructor', 'toString'];
    request.mockResolvedValue({
      id: 't_own_keys',
      recap: '',
      needs: 'ready',
      workflow: { fields: names.map((id) => ({ id })) },
      field_values: {},
      field_approvals: {},
    });
    const record = await run(['ticket', 'show', 't_own_keys'], SERVER);
    expect(record).toMatchObject({ exit_code: 0, stderr: '' });
    for (const name of names) expect(record.stdout).toContain(`  ${name}:\n    character_count: 0`);
    request.mockResolvedValue({
      id: 't_held',
      title: 'Held step',
      recap: 'Awaiting work',
      ticket_status: 'agent_owned',
      agent_state: 'idle',
      stage: 'needs_implementation',
      needs: 'working',
      workflow: { fields: [] },
      field_values: {},
    });
    const held = await run(['ticket', 'show', 't_held', 'recap'], SERVER);
    expect(held.stdout).toContain(
      `  status: ${WORKER_STATUS_LABEL}\n  worker: idle\n  stage: needs_implementation`,
    );
    expect(held.stdout.indexOf('title: Held step')).toBeLessThan(held.stdout.indexOf('status:'));
    const wire = await run(['ticket', 'show', 't_held', '--json'], SERVER);
    expect(JSON.parse(wire.stdout).header).toMatchObject({
      ticket_status: 'agent_owned',
      agent_state: 'idle',
    });
  } finally {
    request.mockRestore();
  }
});

// Gap: HTTP comparisons cannot prove CLI option merging or the credential output policy.
// Reuse the wire stub and stdin seam. No server or external credential store participates.
test('machine commands print the credential once and merge ticket destination inputs', async () => {
  const remote = 'ad1f66de-bcaa-45b7-918a-8d0e7a018ae1';
  const grant = { machine_id: remote, credential: 'synthetic-secret', issued_at_ms: 100 };
  const request = vi.spyOn(http, 'send').mockResolvedValue(grant);
  try {
    const registered = await run(['machine', 'register', 'Build host', '--json'], SERVER);
    expect(JSON.parse(registered.stdout)).toEqual(grant);
    expect(request.mock.lastCall?.slice(1)).toEqual([
      'POST',
      '/api/machines',
      { json_body: { name: 'Build host' } },
    ]);
    const human = await run(['machine', 'replace-credential', remote], SERVER);
    expect(human.stdout.split(grant.credential)).toHaveLength(2);
    expect(request.mock.lastCall?.slice(1)).toEqual(['POST', `/api/machines/${remote}/credential`]);
    const private_grant = { ...grant, credential: 'x'.repeat(43) },
      revision = 'a'.repeat(40);
    request.mockResolvedValueOnce({ app_sha: revision }).mockResolvedValueOnce(private_grant);
    const joined = await run(
      ['machine', 'join-command', 'Build host', '--home', 'https://home.example'],
      SERVER,
    );
    expect(joined.exit_code).toBe(0);
    expect(request.mock.calls.slice(-2).map((call) => call.slice(1))).toEqual([
      ['GET', '/api/meta'],
      ['POST', '/api/machines', { json_body: { name: 'Build host' } }],
    ]);
    const command = joined.stdout
      .split('\n')
      .find((line) => line.startsWith('panels_join_script='))!;
    expect(command).toContain(remote);
    expect(command).toContain(revision);
    expect(command).not.toContain(private_grant.credential);
    expect(joined.stdout.split(private_grant.credential)).toHaveLength(2);
    request.mockClear();
    const invalid_home = await run(
      ['machine', 'join-command', 'Build host', '--home', 'http://home.example'],
      SERVER,
    );
    expect(invalid_home.exit_code).toBe(1);
    expect(request).not.toHaveBeenCalled();

    request.mockResolvedValue({
      id: 't_created',
      stage: 'needs_brief',
      supervision_receipt: 'Owner review',
    });
    // The JSON input rule: stdin when --input-json is absent.
    const args = ['ticket', 'create', '--machine-id', remote];
    const created = await run(args, SERVER, async () => Buffer.from('{"title":"Remote"}'));
    expect(created.exit_code).toBe(0);
    expect(request.mock.lastCall?.slice(1)).toEqual([
      'POST',
      '/api/tickets',
      { json_body: { title: 'Remote', machine_id: remote } },
    ]);
    request.mockClear();
    const duplicate = await run(args, SERVER, async () =>
      Buffer.from(JSON.stringify({ machine_id: remote })),
    );
    expect(duplicate.exit_code).toBe(1);
    expect(duplicate.stderr).toContain('machine_id accepts one input source');
    expect(request).not.toHaveBeenCalled();
  } finally {
    request.mockRestore();
  }
});

// Risk: the shared text rule reads the wrong source, or reads stdin when a flag was given.
test('text input takes --message, --body-file, or stdin, and refuses two sources', async () => {
  const request = vi.spyOn(http, 'send').mockResolvedValue({ fate: 'delivered' });
  const stdin = vi.fn(async () => Buffer.from('From stdin.\r\n'));
  const file = join(mkdtempSync(join(tmpdir(), 'panels-cli-')), 'message.md');
  writeFileSync(file, 'From a file.');
  const message = () => (request.mock.lastCall?.[3]?.json_body as { message?: string })?.message;
  try {
    expect(
      (await run(['send-message', '--owner', '--message', 'Inline.'], SERVER, stdin)).exit_code,
    ).toBe(0);
    expect(message()).toBe('Inline.');
    expect(stdin).not.toHaveBeenCalled();
    await run(['send-message', '--owner', '--body-file', file], SERVER, stdin);
    expect(message()).toBe('From a file.');
    await run(['send-message', '--owner'], SERVER, stdin);
    expect(message()).toBe('From stdin.\n');
    request.mockClear();
    const both = await run(
      ['send-message', '--owner', '--message', 'one', '--body-file', file],
      SERVER,
      stdin,
    );
    expect(both.exit_code).toBe(1);
    expect(both.stderr).toContain('one way only');
    const empty = await run(['send-message', '--owner'], SERVER, async () => Buffer.from(' \n'));
    expect(empty.exit_code).toBe(1);
    expect(request).not.toHaveBeenCalled();
  } finally {
    request.mockRestore();
  }
});

// Risk: the CLI loses the ID that names an accepted message, or misprints an older home's
// answer. Gap: no test read send-message output. The mocked transport is the plain-HTTP path.
test('send-message names its message and prints acceptance and older answers', async () => {
  const request = vi.spyOn(http, 'send');
  const sent = async (answer: Record<string, unknown>, extra: string[] = []) => {
    request.mockResolvedValueOnce(answer);
    const output = await run(['send-message', '--chief', '--message', 'Hi', ...extra], SERVER);
    const options = request.mock.lastCall?.[3] as {
      json_body: { message_id: string };
      request_id: string;
    };
    expect(options.request_id).toBe(options.json_body.message_id);
    return { ...output, id: options.json_body.message_id };
  };
  try {
    const accepted = await sent({ fate: 'accepted', message_id: 'echo' });
    expect(accepted.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(accepted).toMatchObject({
      exit_code: 0,
      stdout: `accepted for delivery · ${accepted.id}\n`,
    });
    const recorded = await sent({ fate: 'recorded' });
    expect(recorded.stdout).toBe(`recorded · ${recorded.id}\n`);
    expect(recorded.id).not.toBe(accepted.id);
    expect((await sent({ fate: 'refused', refusal_reason: 'no_such_conversation' })).stdout).toBe(
      'message refused: no_such_conversation\n',
    );
    expect((await sent({ fate: 'started' })).stdout).toBe('message started\n');
    const json = await sent({ fate: 'queued', queue_position: 2 }, ['--json']);
    expect(JSON.parse(json.stdout)).toEqual({
      fate: 'queued',
      queue_position: 2,
      message_id: json.id,
    });
  } finally {
    request.mockRestore();
  }
});

// Risk: recap writes another record, or writes the owner's when no identity is present.
test('recap writes the caller record for each role and refuses without an identity', async () => {
  const request = vi.spyOn(http, 'send').mockResolvedValue({});
  const stdin = async () => Buffer.from('Done.');
  try {
    const roles: [NodeJS.ProcessEnv, string, string][] = [
      [{ PLAN_ACTOR: 'worker', PLAN_TICKET_ID: 't_me' }, 'PATCH', '/api/tickets/t_me'],
      [{ PLAN_ACTOR: 'manager', PLAN_MANAGER_ID: 'si_me' }, 'PATCH', '/api/managers/si_me'],
      [{ PLAN_ACTOR: 'chief' }, 'PUT', '/api/workers/chief-of-staff/recap'],
    ];
    for (const [identity, method, path] of roles) {
      const result = await run(['recap'], { ...SERVER, ...identity }, stdin);
      expect(result).toMatchObject({ exit_code: 0, stdout: 'recap written\n' });
      expect(request.mock.lastCall?.slice(1)).toEqual([
        method,
        path,
        { json_body: { recap: 'Done.' } },
      ]);
    }
    request.mockClear();
    const nobody = await run(['recap'], SERVER, stdin);
    expect(nobody.exit_code).toBe(1);
    expect(nobody.stderr).toContain('No panels call writes a recap without an agent identity.');
    expect(request).not.toHaveBeenCalled();
  } finally {
    request.mockRestore();
  }
});

// Risk: an approval with no --holder names someone other than the caller, the old word
// "me" (which meant the owner) changes meaning without notice, and the final approval sends
// onward scope. Send-back uses the shared text rule and pins the revision.
test('approve defaults the holder to the caller, refuses me, and send-back sends its guidance', async () => {
  const request = vi.spyOn(http, 'send');
  const detail = (stage: string, field: string, target: string) => ({
    id: 't_cli',
    stage,
    pending_proposal: { field, revision: 2 },
    workflow: { stages: [{ id: stage, gating_field: field }], advance: { [stage]: target } },
  });
  const manager = { ...SERVER, PLAN_ACTOR: 'manager', PLAN_MANAGER_ID: 'si_me' };
  try {
    request
      .mockResolvedValueOnce(detail('needs_implementation', 'implementation', 'needs_consequences'))
      .mockResolvedValueOnce({ id: 't_cli' });
    const refused = await run(
      ['ticket', 'approve', 't_cli', '--ceiling', 'needs_consequences', '--holder', 'me'],
      manager,
    );
    expect(refused.exit_code).toBe(1);
    expect(refused.stderr).toContain('Use --holder owner, --holder chief, or --holder MANAGER_ID');
    request.mockReset();
    request
      .mockResolvedValueOnce(detail('needs_implementation', 'implementation', 'needs_consequences'))
      .mockResolvedValueOnce({ id: 't_cli' });
    const approved = await run(
      ['ticket', 'approve', 't_cli', '--ceiling', 'needs_consequences'],
      manager,
    );
    expect(approved).toMatchObject({ exit_code: 0, stdout: 't_cli approved implementation\n' });
    expect(
      (request.mock.lastCall?.[3]?.json_body as { next_holder?: unknown })?.next_holder,
    ).toEqual({
      kind: 'manager',
      id: 'si_me',
    });
    request.mockResolvedValueOnce(detail('needs_implementation', 'implementation', 'x'));
    const worker = { ...SERVER, PLAN_ACTOR: 'worker', PLAN_TICKET_ID: 't_me' };
    expect((await run(['ticket', 'approve', 't_cli', '--ceiling', 'x'], worker)).exit_code).toBe(1);
    request
      .mockResolvedValueOnce(detail('needs_consequences', 'consequences', 'done'))
      .mockResolvedValueOnce({ id: 't_cli' });
    expect((await run(['ticket', 'approve', 't_cli'], SERVER)).exit_code).toBe(0);
    expect(request.mock.lastCall?.[3]).toEqual({
      json_body: { expected_proposal_revision: 2, decision_id: 't_cli:2:accept' },
    });
    request.mockResolvedValueOnce({ id: 't_cli' });
    const sent_back = await run(
      ['ticket', 'send-back', 't_cli', '--expected-proposal-revision', '2'],
      SERVER,
      async () => Buffer.from('Exact stdin guidance.'),
    );
    expect(sent_back).toMatchObject({ exit_code: 0, stdout: 't_cli proposal sent back\n' });
    expect(request.mock.lastCall?.slice(1)).toEqual([
      'POST',
      '/api/tickets/t_cli/reject',
      {
        json_body: {
          message: 'Exact stdin guidance.',
          expected_proposal_revision: 2,
          decision_id: 't_cli:2:reject',
        },
      },
    ]);
  } finally {
    request.mockRestore();
  }
});

// Risk: manager show names the wrong document as the visible page (the logic that
// manager workspace held), or reads the caller's own Manager when an id is given.
test('manager show defaults to the caller and names the visible page', async () => {
  const request = vi.spyOn(http, 'send').mockResolvedValue({
    id: 'si_me',
    title: 'Mine',
    settled: false,
    goal: 'Purpose',
    recap: 'Recap',
    goals: [{ id: 'goal_1', title: 'Grow', active: true }],
    ticket_needs: { owner_approval: 1, working: 2, done: 4 },
  });
  try {
    const own = await run(['manager', 'show', 'recap'], {
      ...SERVER,
      PLAN_ACTOR: 'manager',
      PLAN_MANAGER_ID: 'si_me',
    });
    expect(request.mock.lastCall?.[3]).toEqual({ params: { detail: 'full', id: 'si_me' } });
    expect(own.stdout).toContain('  visible_page: held goal Grow (goal_1)\n');
    expect(own.stdout).toContain('  page_edit: panels goal edit goal_1\n');
    expect(own.stdout).toContain(`  tickets: 1 Owner: approve, 2 ${WORKER_STATUS_LABEL}; 4 done\n`);
    expect(own.stdout).toContain('parts:\n  recap:\n    value: Recap');
    const page = await run(['manager', 'show', 'si_me', 'page'], SERVER);
    expect(page.stderr).toContain('Use: panels page show --manager MANAGER_ID');
  } finally {
    request.mockRestore();
  }
});

// Risk: during an install this CLI reaches the release before it, which ignores scope and
// omits the need fields. A list would then silently include backlog with no need column.
test('each new read refuses an old-server response instead of printing it', async () => {
  const old_page = complete_page(1);
  const cases: [string[], unknown][] = [
    [
      ['ticket', 'list'],
      { tickets: [{ id: 't_old', stage: 'done', title: 'Old' }], page: old_page },
    ],
    [['ticket', 'show', 't_old'], { id: 't_old', workflow: { fields: [] }, field_values: {} }],
    [['manager', 'list'], { managers: [{ id: 'si_old', title: 'Old' }], page: old_page }],
    [['manager', 'show', 'si_old'], { id: 'si_old', title: 'Old', goal: '', recap: '' }],
    [['review'], { items: [] }],
  ];
  const request = vi.spyOn(http, 'send');
  try {
    for (const [argv, body] of cases) {
      request.mockResolvedValue(body);
      const human = await run(argv, SERVER);
      expect(human, argv.join(' ')).toEqual({
        exit_code: 1,
        stdout: '',
        stderr:
          'error: The Panels server is older than this command. It is being updated. Run the command again in a few minutes.\n',
      });
      const machine = await run([...argv, '--json'], SERVER);
      expect(machine.stdout).toBe('');
      expect(JSON.parse(machine.stderr).error.code).toBe('server_too_old');
    }
  } finally {
    request.mockRestore();
  }
});

// Distinct risk: ambiguous selectors must not turn an individual move into a bulk move.
// The stubbed HTTP boundary also preserves partial failure in JSON and process exit status.
test('machine move-work sends an explicit owner and reports partial failures', async () => {
  const result = {
    owner: { kind: 'chief' },
    title: 'Chief',
    machine_id: 'second',
    moved: true,
    error: 'The new conversation did not start.',
  };
  const request = vi.spyOn(http, 'send').mockResolvedValue(result);
  const environment = { PLAN_SERVER_URL: 'http://127.0.0.1:19020', PLAN_ACTOR: '' };
  try {
    const ambiguous = await run(
      ['machine', 'move-work', 'second', '--chief', '--everything'],
      environment,
    );
    expect(ambiguous.exit_code).not.toBe(0);
    expect(request).not.toHaveBeenCalled();
    const individual = await run(
      ['machine', 'move-work', 'second', '--chief', '--json'],
      environment,
    );
    expect(request).toHaveBeenLastCalledWith(expect.anything(), 'POST', '/api/work-moves', {
      json_body: { owner: { kind: 'chief' }, machine_id: 'second' },
    });
    expect(individual.exit_code).toBe(1);
    expect(JSON.parse(individual.stdout)).toEqual(result);
    request.mockResolvedValueOnce({
      results: [{ ...result, moved: false, error: 'Push work first.' }],
    });
    const batch = await run(['machine', 'move-work', 'second', '--everything'], environment);
    expect(request).toHaveBeenLastCalledWith(
      expect.anything(),
      'POST',
      '/api/work-moves/everything',
      {
        json_body: { machine_id: 'second' },
      },
    );
    expect(batch).toMatchObject({ exit_code: 1, stdout: 'Chief: Push work first.\n' });
  } finally {
    request.mockRestore();
  }
});
