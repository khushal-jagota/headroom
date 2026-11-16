import { expect, test, vi } from 'vitest';
import type { ArtifactEntry } from '../files/contracts.ts';
import { run } from './main.ts';
import * as http from './http.ts';

// Existing workspace comparisons have no artifacts and miss the wrong-field fault.
// A stubbed wire response proves rendering without another server or filesystem host.
test('workspace renders each top-level artifact opens value and retains the JSON tree', async () => {
  const artifacts: ArtifactEntry[] = [
    { name: 'note.md', opens: 'artifacts/note.md', modified_at: 3000, children: [] },
    { name: 'report', opens: 'artifacts/report/index.html', modified_at: 2000, children: [] },
    {
      name: 'group',
      opens: null,
      modified_at: 1000,
      children: [
        {
          name: 'nested.txt',
          opens: 'artifacts/group/nested.txt',
          modified_at: 1000,
          children: [],
        },
      ],
    },
  ];
  const workspace = {
    title: 'Artifact Manager',
    goal: 'Review the artifact tree.',
    recap: 'Files are ready.',
    page_revision: 0,
    goals: [],
    tickets: [],
    artifacts,
  };
  const request = vi.spyOn(http, 'send').mockResolvedValue(workspace);
  try {
    const environment = { PLAN_SERVER_URL: 'http://127.0.0.1:19020', PLAN_ACTOR: '' };
    const human = await run(['manager', 'workspace', 'si_artifacts'], environment);
    expect(human).toMatchObject({ exit_code: 0, stderr: '' });
    expect(human.stdout.split('\n\nArtifacts:\n')[1].split('\n\nTickets:\n')[0]).toBe(
      'artifacts/note.md\nartifacts/report/index.html\nnull',
    );
    const machine = await run(['manager', 'workspace', 'si_artifacts', '--json'], environment);
    expect(machine).toMatchObject({ exit_code: 0, stderr: '' });
    expect(JSON.parse(machine.stdout)).toEqual(workspace);
  } finally {
    request.mockRestore();
  }
});

// Existing populated comparisons use an old fixed server clock. Freeze the CLI clock
// here to prove the age boundary and same-second stage marker without another host.
test('ticket summaries show recap freshness independently of record activity', async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-07-04T12:00:02Z'));
  const base = {
    id: 't_recap',
    stage: 'needs_implementation',
    ticket_status: 'agent',
    title: 'Recap',
  };
  const request = vi.spyOn(http, 'send').mockResolvedValue({
    tickets: [
      {
        ...base,
        recap: 'Current\nrecap',
        recap_updated_at: Math.trunc(Date.now() / 1000),
        recap_stage_changed: true,
      },
      { ...base, recap: 'Older recap', recap_updated_at: null, recap_stage_changed: false },
      { ...base, recap: ' ', recap_updated_at: null, recap_stage_changed: false },
    ],
    page: {
      return_count: 3,
      match_count: 3,
      omitted_before: 0,
      omitted_after: 0,
      complete: true,
      next_offset: null,
    },
  });
  try {
    const output = await run(['ticket', 'list'], {
      PLAN_SERVER_URL: 'http://127.0.0.1:19020',
      PLAN_ACTOR: '',
    });
    expect(output).toMatchObject({ exit_code: 0, stderr: '' });
    expect(output.stdout).toContain('Current recap (recap 0s old, stage changed since)');
    expect(output.stdout).toContain('Older recap (recap age unknown)');
    expect(output.stdout).toContain('\n  no recap');
  } finally {
    request.mockRestore();
    vi.useRealTimers();
  }
});

// Scalar-only checks miss facade exception conversion and Python.get pagination behavior.
test('CLI validates missing native values and skips an absent optional page continuation', async () => {
  const request = vi.spyOn(http, 'send').mockResolvedValue({});
  try {
    const environment = { PLAN_SERVER_URL: 'http://127.0.0.1:19020', PLAN_ACTOR: '' };
    const missing = await run(
      ['project', 'create', '--name', 'Missing wire values', '--json'],
      environment,
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
    const paged = await run(['dataset', 'list'], environment);
    expect(paged).toMatchObject({ exit_code: 0, stderr: '' });
    expect(paged.stdout).toContain('Returned 0 of 0 matches.');
    expect(paged.stdout).not.toMatch(/Use --limit|undefined|\[object Object\]/);
    // Python dict.get never returns inherited values or approval functions.
    const names = ['__proto__', 'constructor', 'toString'];
    request.mockResolvedValue({
      id: 't_own_keys',
      workflow: { fields: names.map((id) => ({ id })) },
      field_values: {},
      field_approvals: {},
    });
    const record = await run(['ticket', 'show', 't_own_keys'], environment);
    expect(record).toMatchObject({ exit_code: 0, stderr: '' });
    for (const name of names) expect(record.stdout).toContain(`  ${name}:\n    character_count: 0`);
  } finally {
    request.mockRestore();
  }
});

// Gap: HTTP comparisons cannot prove CLI option merging or the credential output policy.
// Reuse the wire stub and stdin seam. No server or external credential store participates.
test('machine commands preserve credential JSON and ticket destination inputs', async () => {
  const remote = 'ad1f66de-bcaa-45b7-918a-8d0e7a018ae1';
  const grant = { machine_id: remote, credential: 'synthetic-secret', issued_at_ms: 100 };
  const request = vi.spyOn(http, 'send').mockResolvedValue(grant);
  const environment = { PLAN_SERVER_URL: 'http://127.0.0.1:19020', PLAN_ACTOR: '' };
  try {
    const registered = await run(['machine', 'register', 'Build host'], environment);
    expect(JSON.parse(registered.stdout)).toEqual(grant);
    expect(request.mock.lastCall?.slice(1)).toEqual([
      'POST',
      '/api/machines',
      { json_body: { name: 'Build host' } },
    ]);
    const replaced = await run(['machine', 'replace-credential', remote], environment);
    expect(JSON.parse(replaced.stdout)).toEqual(grant);
    expect(request.mock.lastCall?.slice(1)).toEqual(['POST', `/api/machines/${remote}/credential`]);
    const private_grant = { ...grant, credential: 'x'.repeat(43) },
      revision = 'a'.repeat(40);
    request.mockResolvedValueOnce({ app_sha: revision }).mockResolvedValueOnce(private_grant);
    const joined = await run(
      ['machine', 'join-command', 'Build host', '--home', 'https://home.example'],
      environment,
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
      environment,
    );
    expect(invalid_home.exit_code).toBe(1);
    expect(request).not.toHaveBeenCalled();
    request.mockResolvedValue({
      machines: [{ machine_id: remote, name: 'Build host', available: false }],
    });
    const listed = await run(['machine', 'list'], environment);
    expect(listed.stdout.trim()).toBe(`${remote} Build host Away`);

    request.mockResolvedValue({
      id: 't_created',
      stage: 'needs_brief',
      supervision_receipt: 'Owner review',
    });
    const args = ['ticket', 'create', '--input-json', '-', '--machine-id', remote];
    const created = await run(args, environment, async () => Buffer.from('{"title":"Remote"}'));
    expect(created.exit_code).toBe(0);
    expect(request.mock.lastCall?.slice(1)).toEqual([
      'POST',
      '/api/tickets',
      { json_body: { title: 'Remote', machine_id: remote } },
    ]);
    request.mockClear();
    const duplicate = await run(args, environment, async () =>
      Buffer.from(JSON.stringify({ machine_id: remote })),
    );
    expect(duplicate.exit_code).toBe(1);
    expect(duplicate.stderr).toContain('machine_id accepts one input source');
    expect(request).not.toHaveBeenCalled();
  } finally {
    request.mockRestore();
  }
});

// Gap: API comparisons cannot observe implicit stdin reads or CLI payload omission.
// Reuse the wire stub; explicit guidance and final approval need no connected host.
test('proposal guidance is explicit and final approval omits onward scope', async () => {
  const request = vi.spyOn(http, 'send').mockResolvedValue({ id: 't_cli' });
  const stdin = vi.fn(async () => Buffer.from('Exact stdin guidance.'));
  const env = { PLAN_SERVER_URL: 'http://127.0.0.1:19020', PLAN_ACTOR: '' };
  try {
    expect((await run(['ticket', 'proposal', 't_cli', 'revise'], env, stdin)).exit_code).toBe(1);
    expect(stdin).not.toHaveBeenCalled();
    expect(
      (
        await run(
          ['ticket', 'proposal', 't_cli', 'revise', '--message', 'one', '--body-file', '-'],
          env,
          stdin,
        )
      ).exit_code,
    ).toBe(1);
    expect(
      (await run(['ticket', 'proposal', 't_cli', 'submit', '--message', 'no'], env, stdin))
        .exit_code,
    ).toBe(1);
    expect(
      (
        await run(
          [
            'ticket',
            'proposal',
            't_cli',
            'revise',
            '--body-file',
            '-',
            '--expected-proposal-revision',
            '2',
          ],
          env,
          stdin,
        )
      ).exit_code,
    ).toBe(0);
    expect(request.mock.lastCall?.[3]).toEqual({
      json_body: {
        message: 'Exact stdin guidance.',
        expected_proposal_revision: 2,
        decision_id: 't_cli:2:reject',
      },
    });
    const detail = (stage: string, field: string, target: string) => ({
      id: 't_cli',
      stage,
      pending_proposal: { field, revision: 2 },
      workflow: { stages: [{ id: stage, gating_field: field }], advance: { [stage]: target } },
    });
    request.mockResolvedValueOnce(
      detail('needs_implementation', 'implementation', 'needs_consequences'),
    );
    expect((await run(['ticket', 'proposal', 't_cli', 'accept'], env)).exit_code).toBe(1);
    request
      .mockResolvedValueOnce(detail('needs_consequences', 'consequences', 'done'))
      .mockResolvedValueOnce({ id: 't_cli' });
    expect((await run(['ticket', 'proposal', 't_cli', 'accept'], env)).exit_code).toBe(0);
    expect(request.mock.lastCall?.[3]).toEqual({
      json_body: { expected_proposal_revision: 2, decision_id: 't_cli:2:accept' },
    });
  } finally {
    request.mockRestore();
  }
});
