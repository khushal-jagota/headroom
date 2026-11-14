/** Ordered mutations of two disposable copies. No inference or delivery operations. */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { isDeepStrictEqual, promisify } from 'node:util';

export interface Comparison {
  category: string;
  request: string;
  matched: boolean;
  status?: number[];
  differences?: string[];
}
type Json = Record<string, unknown>;
type Pair<T> = [T, T];
type Instance = { name: string; base: string; env: Record<string, string> };
export async function run_writes(options: {
  instances: Instance[];
  app: string;
  python: string;
  evidence: string;
  results: Comparison[];
  samples: {
    project: string;
    manager: string;
    ticket: string;
    terminal: string;
    existing_ids: string[];
  };
  differences(a: unknown, b: unknown): string[];
  start(item: Instance): Promise<void>;
  stop(item: Instance): Promise<void>;
}) {
  const { instances, evidence, results, samples } = options;
  assert.equal(instances.length, 2);
  for (const key of ['project', 'manager', 'ticket', 'terminal'] as const)
    assert(samples[key], 'Copied corpus needs an existing ' + key);
  const execute = promisify(execFile);
  const mappings = [new Map<string, string>(), new Map<string, string>()];
  const existing = new Set(samples.existing_ids);
  const identifier_fields = new Set([
    'id',
    'ticket_id',
    'project_id',
    'manager_id',
    'goal_id',
    'idea_id',
    'holder_id',
    'owner_id',
    'scope_id',
    'subject_id',
    'target_id',
    'blocked_by_ticket_ids',
    'blocking_ticket_ids',
    'linked_ticket_ids',
    'manager_ids',
  ]);
  function normalized(value: unknown, side: number, field = ''): unknown {
    if (typeof value === 'string')
      return identifier_fields.has(field) ? (mappings[side].get(value) ?? value) : value;
    if (Array.isArray(value)) return value.map((item) => normalized(item, side, field));
    if (value && typeof value === 'object')
      return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [key, normalized(item, side, key)]),
      );
    return value;
  }
  async function record(
    label: string,
    values: Pair<unknown>,
    success = true,
    category = 'write-readback',
    assertion_differences?: string[],
  ) {
    for (let side = 0; side < 2; side++)
      await writeFile(
        join(evidence, `${results.length}-${instances[side].name}.json`),
        JSON.stringify(values[side]),
        { mode: 0o600 },
      );
    const compared = values.map((value, side) => normalized(value, side));
    results.push({
      category,
      request: label,
      matched: success && isDeepStrictEqual(compared[0], compared[1]),
      differences: assertion_differences ?? options.differences(compared[0], compared[1]),
    });
  }
  async function request(side: number, path: string, method = 'GET', body?: unknown, headers = {}) {
    const response = await fetch(instances[side].base + path, {
      method,
      headers: { 'content-type': 'application/json', ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(30_000),
      redirect: 'error',
    });
    const text = await response.text();
    let result: unknown = null;
    if (text) {
      try {
        result = JSON.parse(text);
      } catch {
        result = text;
      }
    }
    return { status: response.status, body: result };
  }
  async function read(label: string, paths: Pair<string>, expected = 200) {
    const pair: Pair<Awaited<ReturnType<typeof request>>> = [
      await request(0, paths[0]),
      await request(1, paths[1]),
    ];
    await record(
      label,
      pair,
      pair.every((value) => value.status === expected),
    );
    return pair.map((value) => value.body as Json) as Pair<Json>;
  }
  let operations = 0,
    refusals = 0;
  async function advance() {
    const now = new Date(Date.parse('2026-11-13T12:00:00Z') + ++operations * 1000).toISOString();
    for (let side = 0; side < 2; side++) {
      const response = await request(side, '/api/test/set-now', 'POST', { now });
      assert.equal(response.status, 200, 'Private test clock must accept the same instant');
    }
  }
  const same = <T>(value: T): Pair<T> => [value, value];
  const paths = (ids: Pair<string>, before: string, after = ''): Pair<string> =>
    ids.map((id) => before + encodeURIComponent(id) + after) as Pair<string>;
  async function mutate(
    label: string,
    method: string,
    urls: Pair<string>,
    bodies: Pair<unknown>,
    settings: {
      actor?: Pair<Record<string, string>>;
      create?: 'project' | 'si' | 't' | 'goal' | 'idea';
      refusal?: boolean;
      readback?: Pair<string>;
      expected_read?: number;
    } = {},
  ) {
    await advance();
    const before =
      settings.refusal && settings.readback
        ? [await request(0, settings.readback[0]), await request(1, settings.readback[1])]
        : undefined;
    const pair: Pair<Awaited<ReturnType<typeof request>>> = [
      await request(0, urls[0], method, bodies[0], settings.actor?.[0]),
      await request(1, urls[1], method, bodies[1], settings.actor?.[1]),
    ];
    let ids: Pair<string> | undefined;
    if (settings.create) {
      if (!pair.every((value) => value.status >= 200 && value.status < 300)) {
        await record(label, pair, false, 'creation-failure');
        throw new Error('Creation failed at ' + label);
      }
      ids = pair.map((value) => (value.body as Json).id) as Pair<string>;
      for (let side = 0; side < 2; side++) {
        assert.equal(typeof ids[side], 'string');
        const pattern =
          settings.create === 'project'
            ? /^project_[a-z0-9_]+$/
            : new RegExp(`^${settings.create}_[0123456789abcdefghjkmnpqrstuvwxyz]{8}$`);
        assert.match(ids[side], pattern, 'New ID format at ' + label);
        assert(!existing.has(ids[side]) && !mappings[side].has(ids[side]), 'New ID must be unique');
        mappings[side].set(ids[side], ids[0]);
      }
    }
    const accepted = pair.every((value) =>
      settings.refusal
        ? value.status >= 400 && value.status < 500
        : value.status >= 200 && value.status < 300,
    );
    if (settings.refusal) refusals++;
    await record(label, pair, accepted, settings.refusal ? 'write-refusal' : 'mutation');
    if (settings.readback) {
      await read(label + ' readback', settings.readback, settings.expected_read);
      if (before) {
        const after = [
          await request(0, settings.readback[0]),
          await request(1, settings.readback[1]),
        ];
        await record(
          label + ' leaves both records unchanged',
          same(null),
          before.every((value, side) => isDeepStrictEqual(value, after[side])),
          'refusal-invariance',
          before.flatMap((value, side) =>
            options.differences(value, after[side]).map((path) => '/' + side + path),
          ),
        );
      }
    }
    if (/proposal|revision/.test(label)) {
      await read(label + ' Review', same('/api/review'));
      const ticket_ids = settings.actor
        ? (settings.actor.map((actor) => actor['x-plan-ticket-id']) as Pair<string>)
        : tickets[0];
      await read(
        label + ' attention',
        paths(ticket_ids, '/api/attention/summary?group_by=ticket&scope_kind=ticket&scope_id='),
      );
    }
    return { ids, bodies: pair.map((value) => value.body as Json) as Pair<Json> };
  }
  async function cli(label: string, args: Pair<string[]>, mutation = false) {
    if (mutation) await advance();
    const pair: unknown[] = [];
    let accepted = true;
    for (let side = 0; side < 2; side++) {
      const item = instances[side];
      let stdout = '',
        stderr = '',
        code: string | number = 0;
      try {
        const output = await execute(
          side === 0 ? options.python : process.execPath,
          side === 0
            ? ['-m', 'planner', ...args[side]]
            : [join(options.app, 'server/cli/main.ts'), ...args[side]],
          { env: item.env, cwd: options.app, timeout: 30_000, maxBuffer: 64 * 1024 * 1024 },
        );
        stdout = output.stdout;
        stderr = output.stderr;
      } catch (error) {
        const failed = error as { stdout?: string; stderr?: string; code?: number | string };
        stdout = failed.stdout ?? '';
        stderr = failed.stderr ?? '';
        code = failed.code ?? 'failed';
      }
      let body: unknown;
      try {
        body = JSON.parse(stdout);
      } catch {
        body = stdout;
        accepted = false;
      }
      accepted &&= code === 0;
      pair.push({ code, body });
      await writeFile(join(evidence, `${results.length}-${item.name}-stderr`), stderr, {
        mode: 0o600,
      });
    }
    await record(
      label,
      pair as Pair<unknown>,
      accepted,
      mutation ? 'cli-mutation' : 'cli-readback',
    );
  }
  const project = (
    await mutate(
      'create Project',
      'POST',
      same('/api/projects'),
      same({ name: 'Gate 3 write replay', summary: 'Private replay Project' }),
      { create: 'project' },
    )
  ).ids!;
  const project_read = paths(project, '/api/projects?detail=full&id=');
  await read('created Project', project_read);
  await mutate(
    'edit Project',
    'PATCH',
    paths(project, '/api/projects/'),
    same({ summary: 'Edited private Project' }),
    { readback: project_read },
  );
  const manager = (
    await mutate(
      'create Manager without a turn',
      'POST',
      same('/api/managers'),
      project.map<Json>((project_id) => ({
        title: 'Gate 3 Manager',
        goal: '',
        project_id,
      })) as Pair<Json>,
      { create: 'si' },
    )
  ).ids!;
  const manager_read = paths(manager, '/api/managers?detail=full&id=');
  await read('created Manager', manager_read);
  await mutate(
    'edit Manager',
    'PATCH',
    paths(manager, '/api/managers/'),
    same({ goal: 'Private saved goal text', recap: 'Private recap' }),
    { readback: manager_read },
  );
  const tickets: Pair<string>[] = [];
  for (const title of ['Gate 3 main Ticket', 'Gate 3 blocker Ticket']) {
    const body = project.map<Json>((project_id, side) => ({
      title,
      worker_type: 'coding',
      locale: 'vps',
      project_id,
      manager: { kind: 'manager', id: manager[side] },
      ceiling_holder: { kind: 'owner', id: 'owner' },
    })) as Pair<Json>;
    tickets.push(
      (await mutate('create ' + title, 'POST', same('/api/tickets'), body, { create: 't' })).ids!,
    );
    await read('created ' + title, paths(tickets.at(-1)!, '/api/tickets?detail=full&id='));
  }
  const ticket = tickets[0],
    other = tickets[1],
    ticket_read = paths(ticket, '/api/tickets?detail=full&id=');
  await mutate(
    'edit Ticket text',
    'PATCH',
    paths(ticket, '/api/tickets/'),
    same({ title: 'Edited Gate 3 Ticket', recap: 'Private Ticket recap' }),
    { readback: ticket_read },
  );
  for (const action of ['backlog', 'resume'])
    await mutate(
      action + ' Ticket',
      'POST',
      paths(ticket, '/api/tickets/', '/' + action),
      same({}),
      { readback: ticket_read },
    );
  const block = ticket.map(
    (id, side) => `/api/collections/blockers/${id}/${other[side]}`,
  ) as Pair<string>;
  for (const method of ['PUT', 'DELETE'])
    await mutate(method + ' dependency', method, block, same(undefined), {
      readback: paths(ticket, '/api/tickets/', '/workspace'),
    });
  const worker = ticket.map<Record<string, string>>((id) => ({
    'x-plan-actor': 'worker',
    'x-plan-ticket-id': id,
  })) as Pair<Record<string, string>>;
  const propose = paths(ticket, '/api/tickets/', '/propose');
  await mutate(
    'wrong actor proposal refusal',
    'POST',
    propose,
    same({ body: 'Owner cannot propose' }),
    { refusal: true, readback: ticket_read },
  );
  const proposal = await mutate(
    'file first proposal',
    'POST',
    propose,
    same({ body: 'First private Brief' }),
    { actor: worker, readback: ticket_read },
  );
  const revisions = proposal.bodies.map(
    (body) => (body.pending_proposal as Json).revision,
  ) as Pair<number>;
  await mutate(
    'reject proposal',
    'POST',
    paths(ticket, '/api/tickets/', '/reject'),
    revisions.map<Json>((expected_proposal_revision) => ({
      expected_proposal_revision,
    })) as Pair<Json>,
    { readback: ticket_read },
  );
  const revised = await mutate(
    'revise proposal',
    'POST',
    propose,
    same({ body: 'Revised private Brief' }),
    { actor: worker, readback: ticket_read },
  );
  const acceptance = (revs: Pair<number>): Pair<Json> =>
    revs.map<Json>((expected_proposal_revision) => ({
      expected_proposal_revision,
      next_ceiling: 'needs_what_changes',
      next_holder: { kind: 'owner', id: 'owner' },
    })) as Pair<Json>;
  await mutate(
    'stale revision refusal',
    'POST',
    paths(ticket, '/api/tickets/', '/accept/brief'),
    acceptance(revisions),
    { refusal: true, readback: ticket_read },
  );
  await mutate(
    'accept current revision',
    'POST',
    paths(ticket, '/api/tickets/', '/accept/brief'),
    acceptance(
      revised.bodies.map((body) => (body.pending_proposal as Json).revision) as Pair<number>,
    ),
    { readback: ticket_read },
  );
  await read('proposal Review projection', same('/api/review'));
  await read(
    'proposal attention projection',
    paths(ticket, '/api/attention/summary?group_by=ticket&scope_kind=ticket&scope_id='),
  );
  const goal = (
    await mutate(
      'create Goal',
      'POST',
      same('/api/goals'),
      project.map<Json>((project_id) => ({
        title: 'Gate 3 Goal',
        project_id,
        kind: 'binary',
        primary_checklist_block_id: null,
      })) as Pair<Json>,
      { create: 'goal' },
    )
  ).ids!;
  const goal_read = paths(goal, '/api/goals/');
  await read('created Goal', goal_read);
  await mutate('edit Goal', 'PATCH', goal_read, same({ why: 'Private goal edit' }), {
    readback: goal_read,
  });
  await mutate(
    'create Idea',
    'POST',
    same('/api/ideas'),
    project.map<Json>((project_id) => ({
      title: 'Gate 3 Idea',
      body: 'Private idea',
      project_id,
    })) as Pair<Json>,
    { create: 'idea' },
  );
  const idea_read = paths(project, '/api/ideas?project_id=');
  await read('created Idea list', idea_read);
  await mutate('delete new Goal', 'DELETE', goal_read, same(undefined), {
    readback: goal_read,
    expected_read: 404,
  });
  for (const [kind, id, field, detail] of [
    ['Project', samples.project, 'summary', '/api/projects?detail=full&id='],
    ['Manager', samples.manager, 'recap', '/api/managers?detail=full&id='],
    ['Ticket', samples.ticket, 'title', '/api/tickets?detail=full&id='],
  ]) {
    const url = same(detail + encodeURIComponent(id));
    const original = await read('copied ' + kind + ' before edit', url);
    const endpoint = same(`/api/${kind.toLowerCase()}s/${encodeURIComponent(id)}`);
    await mutate(
      'edit copied ' + kind,
      'PATCH',
      endpoint,
      same({ [field]: 'Gate 3 temporary copied text' }),
      { readback: url },
    );
    await mutate(
      'restore copied ' + kind + ' text',
      'PATCH',
      endpoint,
      original.map((body) => ({ [field]: body[field] })) as Pair<Json>,
      { readback: url },
    );
  }
  const terminal = same('/api/tickets?detail=full&id=' + encodeURIComponent(samples.terminal));
  await mutate(
    'copied terminal Ticket refusal',
    'POST',
    same(`/api/tickets/${encodeURIComponent(samples.terminal)}/backlog`),
    same({}),
    { refusal: true, readback: terminal },
  );
  await cli(
    'CLI Project edit',
    project.map((id) => [
      'project',
      'set',
      id,
      'summary',
      '--value',
      'CLI saved Project summary',
      '--json',
    ]) as Pair<string[]>,
    true,
  );
  await read('CLI Project HTTP readback', project_read);
  await cli(
    'CLI Project readback',
    project.map((id) => ['project', 'show', id, '--json']) as Pair<string[]>,
  );
  await cli(
    'CLI Ticket backlog',
    other.map((id) => ['ticket', 'backlog', id, '--json']) as Pair<string[]>,
    true,
  );
  await read('CLI Ticket HTTP readback', paths(other, '/api/tickets?detail=full&id='));
  await cli(
    'CLI Ticket readback',
    other.map((id) => ['ticket', 'show', id, '--json']) as Pair<string[]>,
  );
  const persisted = [
    project_read,
    manager_read,
    ticket_read,
    paths(other, '/api/tickets?detail=full&id='),
    idea_read,
    same('/api/projects?detail=full&id=' + samples.project),
    same('/api/managers?detail=full&id=' + samples.manager),
    same('/api/tickets?detail=full&id=' + samples.ticket),
  ];
  const before_restart: Pair<Json>[] = [];
  for (let index = 0; index < persisted.length; index++)
    before_restart.push(await read('saved record before restart ' + index, persisted[index]));
  for (const item of instances) await options.stop(item);
  for (const item of instances) await options.start(item);
  // Restore the same domain instant after each fresh TestClock starts.
  const now = new Date(Date.parse('2026-11-13T12:00:00Z') + operations * 1000).toISOString();
  for (let side = 0; side < 2; side++)
    assert.equal((await request(side, '/api/test/set-now', 'POST', { now })).status, 200);
  for (let index = 0; index < persisted.length; index++) {
    const after = await read('saved record after restart ' + index, persisted[index]);
    await record(
      'restart preserves each record ' + index,
      same(null),
      after.every((value, side) => isDeepStrictEqual(value, before_restart[index][side])),
      'restart-persistence',
      after.flatMap((value, side) =>
        options.differences(before_restart[index][side], value).map((path) => '/' + side + path),
      ),
    );
  }
  await read('deleted Goal stays deleted after restart', goal_read, 404);
  return {
    operations,
    refusals,
    restart_records: persisted.length,
    id_pairs: mappings[0].size,
    identifier_fields: [...identifier_fields],
    unchecked: ['conversation writes', 'artifact content', 'real delivery'],
  };
}
