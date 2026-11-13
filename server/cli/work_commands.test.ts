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
      'artifacts/note.md\nartifacts/report/index.html\nNone',
    );
    const machine = await run(['manager', 'workspace', 'si_artifacts', '--json'], environment);
    expect(machine).toMatchObject({ exit_code: 0, stderr: '' });
    expect(JSON.parse(machine.stdout)).toEqual(workspace);
  } finally {
    request.mockRestore();
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
