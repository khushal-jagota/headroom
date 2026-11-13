/** The HTTP-client commands used for Panels work. Domain decisions stay on the server. */
import type { CommandResult, Invocation } from './contracts.ts';
import { send, send_text, emit, fail_validation, json } from './http.ts';
import { read_source, read_json_object, worker_stdin_body } from './input.ts';
import { part, parse_part_names, project_record, render_text } from './record_projection.ts';
import type { RecordPart } from './record_projection.ts';
import { JsonNumber, numeric_value, parse_numeric_json } from '../core/http/numeric_json.ts';
import { python_text as scalar } from './python_text.ts';
import { ticket_argument } from './parser.ts';

type Body = Record<string, any>;
const python_sort = (left: string, right: string): number => {
  const a = Array.from(left, (c) => c.codePointAt(0)!);
  const b = Array.from(right, (c) => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
};
const quote = (value: string, slash = false): string => {
  const encoded = encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
  );
  return slash ? encoded.replace(/%2F/g, '/') : encoded;
};
const drop_none = (body: Body): Body =>
  Object.fromEntries(
    Object.entries(body).filter(([, value]) => value !== null && value !== undefined),
  );
const lines = (rows: any[], format: (row: any) => string): string =>
  rows.length ? rows.map(format).join('\n') : '(none)';
const raw = (stdout: string): CommandResult => ({ exit_code: 0, stdout, stderr: '' });
const ticket_id = (inv: Invocation, positional: string | null | undefined): string => {
  const value = ticket_argument(positional, inv.environment);
  if (value !== undefined) return value;
  return fail_validation(inv, 'ticket id required: pass it or set PLAN_TICKET_ID');
};
function caller_principal(inv: Invocation): Body {
  const actor = inv.environment.PLAN_ACTOR?.trim();
  const tid = inv.environment.PLAN_TICKET_ID?.trim();
  const mid = inv.environment.PLAN_MANAGER_ID?.trim();
  if (actor === 'chief') return { kind: 'chief', id: 'chief' };
  if (actor === 'worker' && tid) return { kind: 'ticket', id: tid };
  if (actor === 'manager' && mid) return { kind: 'manager', id: mid };
  return { kind: 'owner', id: 'owner' };
}
function holder(inv: Invocation, value: string): Body {
  value = value.trim();
  if (['me', 'owner'].includes(value.toLowerCase())) return { kind: 'owner', id: 'owner' };
  if (value.toLowerCase() === 'chief') return { kind: 'chief', id: 'chief' };
  if (value.startsWith('si_')) return { kind: 'manager', id: value };
  return fail_validation(inv, 'holder must be me, chief, or a Manager id');
}
function bounded(rows: string, page: Body): string {
  const result =
    numeric_value(page.match_count) === 0
      ? 'No matches.'
      : numeric_value(page.return_count) === 0
        ? 'No results at this offset.'
        : rows;
  const facts = `Returned ${scalar(page.return_count)} of ${scalar(page.match_count)} matches. Omitted ${scalar(page.omitted_before)} before and ${scalar(page.omitted_after)} after. Complete: ${scalar(page.complete ? 'yes' : 'no')}.`;
  const directions: string[] = [];
  if (numeric_value(page.omitted_before))
    directions.push('Use --offset 0 to start from the first match.');
  if (page.next_offset !== null)
    directions.push(
      `Use --limit ${scalar(page.limit)} --offset ${scalar(page.next_offset)} for the next page.`,
    );
  return [result, facts, ...directions].join('\n');
}
async function value_or_file(
  inv: Invocation,
  field: string,
  allow_clear = true,
): Promise<string | null> {
  const { value, body_file, clear } = inv.values;
  if (Number(value != null) + Number(body_file != null) + Number(allow_clear && clear) !== 1)
    fail_validation(
      inv,
      `set ${scalar(field)} with exactly one of --value, --body-file, or --clear`,
    );
  if (allow_clear && clear) return null;
  return body_file != null ? read_source(inv, body_file) : value;
}
function record_parts(inv: Invocation, names: string | null | undefined): string[] | null {
  try {
    return parse_part_names(names ?? null);
  } catch (error) {
    return fail_validation(inv, (error as Error).message);
  }
}
function emit_record(
  inv: Invocation,
  header: Body,
  parts: Record<string, RecordPart>,
  names: string | null | undefined,
): CommandResult {
  let projection: Body;
  const requested = record_parts(inv, names);
  try {
    projection = project_record(header, parts, requested);
  } catch (error) {
    const no_route = (names?.split(',') ?? []).includes('rejection')
      ? 'Revision feedback arrives in the Worker prompt. No panels command reads it.'
      : undefined;
    return fail_validation(inv, (error as Error).message, { no_route });
  }
  return emit(projection, inv.values.as_json, render_text(projection));
}
async function ticket_record(
  inv: Invocation,
  data: Body,
  names: string | null | undefined,
): Promise<[Body, Record<string, RecordPart>]> {
  const keys = [
    'id',
    'title',
    'worker_type',
    'locale',
    'employee_backend',
    'employee_launch_model',
    'employee_launch_reasoning_effort',
    'stage',
    'ticket_status',
    'is_backlogged',
    'record_sha256',
    'deadline',
    'project_id',
    'manager_id',
    'ceiling',
    'ceiling_holder',
  ];
  const header = Object.fromEntries(
    keys.filter((key) => key in data).map((key) => [key, data[key]]),
  );
  const parts: Record<string, RecordPart> = {};
  for (const field of data.workflow.fields)
    parts[field.id] = part(data.field_values[field.id] ?? null, {
      approval: data.field_approvals?.[field.id],
    });
  parts.workflow = part(data.workflow ?? null);
  parts.proposal = part(data.pending_proposal ?? null);
  parts.recap = part(data.recap ?? '');
  for (const field of new Set(record_parts(inv, names) ?? [])) {
    if (field in parts || field === 'rejection') continue;
    const body = await send(
      inv,
      'GET',
      `/api/tickets/${quote(String(data.id))}/workspace/fields/${quote(field)}`,
    );
    parts[field] = part(body.value);
  }
  return [header, parts];
}
async function show_ticket(inv: Invocation, worker_self = false): Promise<CommandResult> {
  let { ticket_id: tid, part_names: names } = inv.values;
  let named = tid != null && tid.startsWith('t_');
  if (worker_self) {
    tid = inv.environment.PLAN_TICKET_ID?.trim();
    if (!tid)
      fail_validation(
        inv,
        'no ticket worker identity in env (PLAN_TICKET_ID is missing); not running as a ticket worker',
        { no_route: 'No panels worker call is available until the process has a Ticket identity.' },
      );
    named = false;
  } else if (tid != null && !named) {
    if (names != null)
      fail_validation(inv, 'a current-Ticket part list cannot be followed by another argument');
    names = tid;
    tid = null;
  }
  if (!worker_self) tid = ticket_id(inv, tid);
  const data = await send(
    inv,
    'GET',
    named ? '/api/tickets' : `/api/tickets/${tid}/worker-self`,
    named ? { params: { detail: 'full', id: tid } } : {},
  );
  const [header, parts] = await ticket_record(inv, data, names);
  return emit_record(inv, header, parts, names);
}
async function skill_patch(inv: Invocation): Promise<Body> {
  const patch: Body = {};
  if (inv.values.description != null) {
    if (!inv.values.description.trim()) fail_validation(inv, 'description must not be blank');
    patch.description = inv.values.description;
  }
  const text = process.stdin.isTTY
    ? ''
    : (await inv.stdin()).toString('utf8').replace(/\r\n?/g, '\n');
  if (text) {
    if (!text.trim()) fail_validation(inv, 'markdown_body must not be blank');
    patch.markdown_body = text;
  }
  if (!Object.keys(patch).length)
    fail_validation(inv, 'supply --description or a skill body on redirected stdin');
  return patch;
}
async function submit_proposal(
  inv: Invocation,
  tid: string | null | undefined,
): Promise<CommandResult> {
  tid = ticket_id(inv, tid);
  if (inv.values.body_file != null)
    fail_validation(inv, '--body-file is no longer supported: pipe proposal text on stdin instead');
  if (inv.values.recap != null || inv.values.recap_file != null)
    fail_validation(
      inv,
      'a proposal no longer carries the recap: use `panels worker recap` instead',
    );
  const body = await worker_stdin_body(inv, 'proposal body');
  const data = await send(inv, 'POST', `/api/tickets/${tid}/propose`, { json_body: { body } });
  return emit(data, inv.values.as_json, `proposed on ${scalar(data.id)}`);
}
async function request_help(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const tid = ticket_id(inv, v.ticket_id);
  const selected =
    Number(!!v.owner) +
    Number(!!v.chief) +
    Number(v.recipient_ticket_id != null) +
    Number(v.recipient_manager_id != null);
  if (selected > 1) fail_validation(inv, 'request-help accepts at most one recipient');
  const recipient = v.owner
    ? { kind: 'owner', id: 'owner' }
    : v.chief
      ? { kind: 'chief', id: 'chief' }
      : v.recipient_ticket_id != null
        ? { kind: 'ticket', id: v.recipient_ticket_id }
        : v.recipient_manager_id != null
          ? { kind: 'manager', id: v.recipient_manager_id }
          : null;
  const message = await worker_stdin_body(inv, 'help message');
  const body: Body = { message };
  if (recipient !== null) body.recipient = recipient;
  const data = await send(inv, 'POST', `/api/tickets/${tid}/request-help`, { json_body: body });
  return emit(data, v.as_json, `help message ${scalar(data.fate)}`);
}
async function proposal(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const args = v.proposal_args as string[];
  let tid: string | null;
  let action: string;
  if (args.length === 1) [tid, action] = [null, args[0]];
  else if (args.length === 2) [tid, action] = args;
  else
    return fail_validation(inv, 'proposal requires ACTION, with an optional Ticket id before it');
  if (!['submit', 'accept', 'revise'].includes(action))
    fail_validation(inv, 'ACTION must be submit, accept, or revise');
  if (
    action !== 'accept' &&
    [v.ceiling, v.holder, v.edit_file, v.kickoff_title, v.kickoff_note_file].some(
      (value) => value != null,
    )
  )
    fail_validation(inv, `${scalar(action)} does not accept proposal approval options`);
  if (action === 'submit') return submit_proposal(inv, tid);
  tid = ticket_id(inv, tid);
  if (action === 'revise') {
    const text = await read_source(inv, '-');
    const data = await send(inv, 'POST', `/api/tickets/${tid}/reject`, {
      json_body: { message: text },
    });
    return emit(data, v.as_json, `${scalar(tid)} proposal rejected`);
  }
  const detail = await send(inv, 'GET', '/api/tickets', { params: { detail: 'full', id: tid } });
  const stage = detail.stage;
  const field =
    detail.workflow.stages.find((candidate: Body) => candidate.id === stage)?.gating_field ?? null;
  if (field === null)
    fail_validation(inv, `ticket in ${scalar(stage)} has nothing to approve`, {
      no_route: 'No panels approval call is available for this Ticket.',
    });
  if (detail.pending_proposal === null || detail.pending_proposal.field !== field)
    fail_validation(inv, `no pending ${scalar(field)} proposal`, {
      no_route: 'No panels approval call is available until a proposal exists.',
    });
  if (v.ceiling == null) fail_validation(inv, 'approval requires --ceiling');
  if (v.kickoff_title != null) {
    if (field !== 'brief')
      fail_validation(inv, '--kickoff-title only applies while approving the brief');
    await send(inv, 'PATCH', `/api/tickets/${tid}`, { json_body: { title: v.kickoff_title } });
  }
  const payload: Body = {
    next_ceiling: v.ceiling,
    next_holder: v.holder != null ? holder(inv, v.holder) : caller_principal(inv),
  };
  if (v.edit_file != null) payload.edited_body = await read_source(inv, v.edit_file);
  if (v.kickoff_note_file != null) {
    if (field !== 'brief')
      fail_validation(inv, '--kickoff-note-file only applies while approving the brief');
    if ('edited_body' in payload)
      fail_validation(inv, 'approval accepts only one edited body option');
    payload.edited_body = await read_source(inv, v.kickoff_note_file);
  }
  const data = await send(inv, 'POST', `/api/tickets/${tid}/accept/${field}`, {
    json_body: payload,
  });
  return emit(data, v.as_json, `${scalar(data.id)} approved ${scalar(field)}`);
}
function artifact_paths(entries: Body[]): string[] {
  const paths: string[] = [];
  for (const entry of entries) {
    if (typeof entry.opens === 'string' && entry.opens.startsWith('artifacts/'))
      paths.push(entry.opens.slice(10));
    paths.push(...artifact_paths(entry.children));
  }
  return paths;
}
async function manager_show(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const data = await send(inv, 'GET', '/api/managers', {
    params: { detail: 'full', id: v.item_id },
  });
  const header: Body = {};
  for (const key of ['id', 'title', 'project_id', 'project'])
    if (key in data) header[key] = data[key];
  header.settled = data.settled;
  for (const key of ['page_revision', 'page_character_count'])
    if (key in data) header[key] = data[key];
  if (data.manager != null)
    Object.assign(header, {
      manager_agent_key: data.manager.agent_key,
      manager_conversation_id: data.manager.conversation_id,
      employee_backend: data.manager.launch_configuration.employee_backend,
      employee_launch_model: data.manager.launch_configuration.employee_launch_model,
      employee_launch_reasoning_effort:
        data.manager.launch_configuration.employee_launch_reasoning_effort,
    });
  const parts: Record<string, RecordPart> = { goal: part(data.goal), recap: part(data.recap) };
  const requested = record_parts(inv, v.part_names);
  if (requested === null) {
    const projection: Body = project_record(header, parts, null);
    projection.manifest.page = { character_count: data.page_character_count ?? 0 };
    projection.header.page_edit = `panels page edit --manager ${v.item_id}`;
    projection.header.visible_page = `panels manager workspace ${v.item_id}`;
    return emit(projection, v.as_json, render_text(projection));
  }
  try {
    project_record(header, { ...parts, page: part(null) }, requested);
  } catch (error) {
    fail_validation(inv, (error as Error).message);
  }
  if (requested.includes('page')) {
    const saved = await send(inv, 'GET', `/api/managers/${quote(v.item_id)}/page`);
    parts.page = part(saved.page);
    header.page_revision = saved.page_revision;
  }
  return emit_record(inv, header, parts, v.part_names);
}
async function manager_workspace(inv: Invocation): Promise<CommandResult> {
  const id = inv.values.item_id;
  const data = await send(inv, 'GET', `/api/managers/${id}/workspace`);
  const goals = data.goals;
  let visible: string;
  if (goals.length === 1) {
    const goal = goals[0];
    visible = `Visible page: held goal ${scalar(goal.title)} (${scalar(goal.id)}). The Manager's own document is hidden.\nRead: panels goal show ${scalar(goal.id)}\nSchema: panels goal schema edit\nEdit: panels goal edit ${scalar(goal.id)}`;
  } else
    visible = `Visible page: this Manager's own document.\nRead: panels page show --manager ${scalar(id)}\nSchema: panels page schema\nEdit: panels page edit --manager ${scalar(id)}`;
  const human =
    `${scalar(data.title)} (${scalar(id)})\nPurpose: ${scalar(data.goal)}\nRecap: ${scalar(data.recap)}\n\n${scalar(visible)}\nOwn page revision: ${scalar(data.page_revision)}\nEdits replace the complete document. Artifacts are supporting files.\n\nHeld goals:\n` +
    lines(goals, (goal) => `${scalar(goal.id)} ${scalar(goal.title)}`) +
    '\n\nArtifacts:\n' +
    lines(data.artifacts, (artifact) => scalar(artifact.opens)) +
    '\n\nTickets:\n' +
    lines(
      data.tickets,
      (ticket) => `${scalar(ticket.id)} ${scalar(ticket.title)} [${scalar(ticket.stage)}]`,
    );
  return emit(data, inv.values.as_json, human);
}

export async function run_work_command(inv: Invocation): Promise<CommandResult> {
  const path = inv.command.path.join(' ');
  const v = inv.values;
  const as_json = v.as_json;
  let data: Body;
  switch (path) {
    case 'send-message': {
      const selected =
        Number(!!v.owner) +
        Number(!!v.chief) +
        Number(v.ticket_id != null) +
        Number(v.manager_id != null);
      if (selected !== 1)
        fail_validation(
          inv,
          'send-message requires exactly one of --owner, --chief, --ticket, or --manager',
        );
      if ((v.message == null) === (v.body_file == null))
        fail_validation(inv, 'send-message requires exactly one of --message or --body-file');
      const text = v.message ?? (await read_source(inv, v.body_file ?? ''));
      if (!text.trim()) fail_validation(inv, 'empty message');
      const target = v.owner
        ? { kind: 'owner', id: 'owner' }
        : v.chief
          ? { kind: 'chief', id: 'chief' }
          : v.ticket_id != null
            ? { kind: 'ticket', id: v.ticket_id }
            : { kind: 'manager', id: v.manager_id };
      data = await send(inv, 'POST', '/api/messages/send', {
        json_body: { target, message: text, mode: v.mode },
      });
      return emit(
        data,
        as_json,
        `message ${scalar(data.fate)}` +
          (data.fate === 'refused' ? `: ${scalar(data.refusal_reason)}` : ''),
      );
    }
    case 'worker-type list':
      data = await send(inv, 'GET', '/api/worker-types?supported=true');
      return emit(
        data,
        as_json,
        lines(data.worker_types, (item) => scalar(item.worker_type)),
      );
    case 'worker-type show':
      data = await send(inv, 'GET', `/api/worker-types/${v.worker_type}`);
      return emit(data, as_json, json(data, { indent: 2, ensure_ascii: false }));
    case 'worker-type save': {
      const body = await worker_stdin_body(inv, 'worker type record');
      let record: any;
      try {
        record = parse_numeric_json(body);
      } catch {
        return fail_validation(inv, 'worker type record must be JSON');
      }
      if (
        !record ||
        typeof record !== 'object' ||
        Array.isArray(record) ||
        record instanceof JsonNumber
      )
        fail_validation(inv, 'worker type record must be a JSON object');
      data = await send(inv, 'POST', '/api/worker-types', { json_body: record });
      return emit(data, as_json, `${scalar(data.worker_type)} saved`);
    }
    case 'worker-type delete':
      data = await send(inv, 'DELETE', `/api/worker-types/${quote(v.worker_type)}`);
      return emit(data, as_json, `${scalar(v.worker_type)} deleted`);
    case 'worker-type skill': {
      const patch = await skill_patch(inv);
      const worker_path = quote(v.worker_type);
      const definition = await send(inv, 'GET', `/api/worker-types/${worker_path}`);
      const name = definition.profile.specialist_skill;
      data = await send(inv, 'PATCH', `/api/skills/${quote(name)}`, { json_body: patch });
      if (as_json) data = (await send(inv, 'GET', `/api/workers/${worker_path}`)).settings;
      return emit(data, as_json, `${scalar(name)} saved`);
    }
    case 'skill list':
      data = await send(inv, 'GET', '/api/skills');
      return emit(
        data,
        as_json,
        lines(data.skills, (item) => scalar(item.name)),
      );
    case 'skill show':
      data = await send(inv, 'GET', `/api/skills/${quote(v.name)}`);
      return as_json ? emit(data, true, '') : raw(data.markdown_body);
    case 'skill edit':
      data = await send(inv, 'PATCH', `/api/skills/${quote(v.name)}`, {
        json_body: await skill_patch(inv),
      });
      return emit(data, as_json, `${scalar(data.name)} saved`);
    case 'skill apply': {
      let manifest: any;
      try {
        manifest = parse_numeric_json(await read_source(inv, v.input_json));
      } catch (error) {
        if (error instanceof SyntaxError)
          return fail_validation(inv, 'manifest must contain a JSON object');
        throw error;
      }
      if (
        !manifest ||
        typeof manifest !== 'object' ||
        Array.isArray(manifest) ||
        manifest instanceof JsonNumber
      )
        fail_validation(inv, 'manifest must contain a JSON object');
      data = await send(inv, 'POST', '/api/skills/apply', { json_body: manifest });
      return emit(data, as_json, `applied ${scalar(data.skills.length)} skills`);
    }
    case 'skill delete':
      data = await send(inv, 'DELETE', `/api/skills/${quote(v.name)}`);
      return emit(data, as_json, `${scalar(data.name)} deleted`);
    case 'skill write-file': {
      const content = process.stdin.isTTY
        ? ''
        : (await inv.stdin()).toString('utf8').replace(/\r\n?/g, '\n');
      if (!content.trim()) fail_validation(inv, 'supply the file text on redirected stdin');
      data = await send(inv, 'PUT', `/api/skills/${quote(v.name)}/files`, {
        json_body: { path: v.path, content },
      });
      return emit(data, as_json, `${scalar(data.name)}/${scalar(data.path)} written`);
    }
    case 'skill check': {
      data = await send(inv, 'GET', '/api/skills/stale');
      const refs = data.stale_references;
      const human = !refs.length
        ? 'every skill row names only things this build has'
        : refs
            .map(
              (item: Body) =>
                `${scalar(item.skill_name)}: ${scalar(item.kind)} ${scalar(item.reference)}`,
            )
            .join('\n') +
          `\n\n${scalar(data.skill_names.length)} skills hold something this build does not have`;
      return emit(data, as_json, human);
    }
    case 'project list':
      data = await send(inv, 'GET', '/api/projects', {
        params: { detail: 'summary', limit: v.limit, offset: v.offset },
      });
      return emit(
        data,
        as_json,
        bounded(
          lines(data.projects, (row) => `${scalar(row.id)} ${scalar(row.name)}`),
          data.page,
        ),
      );
    case 'project show': {
      data = await send(inv, 'GET', '/api/projects', {
        params: { detail: 'full', id: v.project_id },
      });
      const header = Object.fromEntries(
        ['id', 'name', 'folder_path'].filter((key) => key in data).map((key) => [key, data[key]]),
      );
      return emit_record(inv, header, { summary: part(data.summary) }, v.part_names);
    }
    case 'project create':
      data = await send(inv, 'POST', '/api/projects', {
        json_body: drop_none({ name: v.name, summary: v.summary, folder_path: v.folder_path }),
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(data.name)}`);
    case 'project set': {
      const value = await value_or_file(inv, v.field);
      if (v.field === 'name' && value === null)
        fail_validation(inv, `${scalar(v.field)} cannot be cleared`);
      const field = v.field === 'folder-path' ? 'folder_path' : v.field;
      data = await send(inv, 'PATCH', `/api/projects/${v.project_id}`, {
        json_body: { [field]: v.field === 'folder-path' && value === null ? null : (value ?? '') },
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(v.field)} set`);
    }
    case 'ticket create': {
      const body = await read_json_object(inv, v.input_json, 'Ticket create input');
      if (v.locale != null) {
        if ('locale' in body) fail_validation(inv, 'locale accepts one input source');
        body.locale = v.locale;
      }
      data = await send(inv, 'POST', '/api/tickets', { json_body: body });
      return emit(
        data,
        as_json,
        `${scalar(data.id)} ${scalar(data.stage)}\n${scalar(data.supervision_receipt)}`,
      );
    }
    case 'ticket show':
      return show_ticket(inv);
    case 'ticket backlog':
    case 'ticket resume': {
      const tid = ticket_id(inv, v.ticket_id);
      const action = path.split(' ')[1];
      data = await send(inv, 'POST', `/api/tickets/${tid}/${action}`, { json_body: {} });
      return emit(data, as_json, action === 'backlog' ? 'Ticket backlogged.' : 'Ticket resumed.');
    }
    case 'ticket delete': {
      const tid = ticket_id(inv, v.ticket_id);
      data = await send(inv, 'DELETE', `/api/tickets/${tid}`, {
        params: v.force ? { force: true } : undefined,
      });
      return emit(data, as_json, `${scalar(tid)} permanently deleted`);
    }
    case 'ticket list': {
      const params: Body = {
        is_backlogged: v.is_backlogged,
        worker_type: v.worker_type?.length ? v.worker_type : null,
        stage: v.stage?.length ? v.stage : null,
        exclude_stage: v.exclude_stage?.length ? v.exclude_stage : null,
        ticket_status: v.ticket_status?.length ? v.ticket_status : null,
        exclude_ticket_status: v.exclude_ticket_status?.length ? v.exclude_ticket_status : null,
        include_terminal: v.include_terminal || null,
        search: v.search,
        project: v.project,
        project_id: v.project_id,
        manager_id: v.manager,
        limit: v.limit,
        offset: v.offset,
      };
      data = await send(inv, 'GET', '/api/tickets', {
        params: { detail: 'summary', ...drop_none(params) },
      });
      const rows = lines(
        data.tickets,
        (ticket) =>
          `${scalar(ticket.id)} ${scalar(ticket.stage)} ${scalar(ticket.ticket_status)} ${scalar(ticket.title)}` +
          (ticket.is_backlogged ? ' | backlogged' : '') +
          (ticket.project ? ` | ${scalar(ticket.project)}` : '') +
          (ticket.recap_preview ? `\n  ${scalar(ticket.recap_preview)}` : ''),
      );
      return emit(data, as_json, bounded(rows, data.page));
    }
    case 'ticket edit': {
      const tid = ticket_id(inv, v.ticket_id);
      const body = await read_json_object(inv, v.input_json, 'Ticket edit input');
      if (!Object.keys(body).length)
        fail_validation(inv, 'Ticket edit input must contain at least one field');
      data = await send(inv, 'PATCH', `/api/tickets/${tid}`, { json_body: body });
      return emit(data, as_json, `${scalar(data.id)} edited`);
    }
    case 'ticket complete':
      data = await send(inv, 'POST', `/api/tickets/${v.ticket_id}/complete/${v.field}`, {
        json_body: { body: await value_or_file(inv, v.field, false) },
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(v.field)} completed`);
    case 'ticket employee-configuration':
      data = await send(inv, 'PUT', `/api/tickets/${v.ticket_id}/employee-configuration`, {
        json_body: {
          employee_backend: v.backend,
          employee_launch_model: v.model,
          employee_launch_reasoning_effort: v.reasoning_effort,
        },
      });
      return emit(
        data,
        as_json,
        `${scalar(data.id)} employee configuration set ${scalar(v.backend)} ${scalar(v.model)}`,
      );
    case 'ticket history': {
      const tid = ticket_id(inv, v.ticket_id);
      if (v.after != null && v.before != null)
        fail_validation(inv, 'history reads one direction: --after or --before');
      const detail = await send(inv, 'GET', '/api/tickets', {
        params: { detail: 'full', id: tid },
      });
      const cid = detail.conversation_id;
      if (!cid)
        fail_validation(inv, 'the Ticket has no current Worker conversation', {
          no_route: 'No panels history call is available until a Worker conversation exists.',
        });
      const params =
        v.after != null
          ? { after: v.after, limit: v.limit }
          : drop_none({ limit: v.limit, before: v.before });
      const page = await send(inv, 'GET', `/api/conversation/conversations/${cid}/events`, {
        params,
      });
      data = { ticket_id: tid, conversation_id: cid, events: page.events, has_more: page.has_more };
      return emit(data, as_json, `${scalar(tid)} ${scalar(data.events.length)} history events`);
    }
    case 'ticket restart-worker': {
      if ((v.backend == null) !== (v.model == null))
        fail_validation(inv, 'restart-worker needs --backend and --model together, or neither');
      const body =
        v.backend != null
          ? {
              employee_backend: v.backend,
              employee_launch_model: v.model,
              employee_launch_reasoning_effort: v.reasoning_effort,
            }
          : {};
      data = await send(inv, 'POST', `/api/tickets/${v.ticket_id}/restart-worker`, {
        json_body: body,
      });
      const configuration = data.employee_configuration;
      const launch = `${scalar(configuration.employee_backend)} ${scalar(configuration.employee_launch_model)}`;
      let human = data.started
        ? `${scalar(v.ticket_id)} restarted on ${scalar(launch)}`
        : data.delivery_fate === 'uncertain'
          ? `${scalar(v.ticket_id)} start delivery is uncertain. The Worker claim remains held.`
          : data.delivery_fate === 'refused'
            ? `${scalar(v.ticket_id)} start delivery was refused`
            : `${scalar(v.ticket_id)} did not start: ${scalar(data.not_started_because)}`;
      if (data.revision_restarted_in_fresh_conversation)
        human +=
          '\nThe revision continues in a fresh conversation. The old working context is gone, and only the revision feedback carries over.';
      return emit(data, as_json, human);
    }
    case 'ticket block':
    case 'ticket unblock': {
      data = await send(
        inv,
        path === 'ticket block' ? 'PUT' : 'DELETE',
        `/api/collections/blockers/${v.ticket_id}/${v.blocker_id}`,
      );
      return emit(
        data,
        as_json,
        path === 'ticket block'
          ? `${scalar(v.ticket_id)} blocked by ${scalar(v.blocker_id)}`
          : `${scalar(v.ticket_id)} unblocked from ${scalar(v.blocker_id)}`,
      );
    }
    case 'ticket proposal':
      return proposal(inv);
    case 'ticket request-help':
    case 'worker request-help':
      return request_help(inv);
    case 'ticket assign': {
      if (!!v.chief === (v.item_id != null))
        fail_validation(inv, 'use exactly one of --chief or --manager');
      const parent = v.chief ? { kind: 'chief', id: 'chief' } : { kind: 'manager', id: v.item_id };
      data = await send(inv, 'PATCH', `/api/tickets/${v.ticket_id}`, {
        json_body: { manager: parent },
      });
      return emit(data, as_json, `${scalar(v.ticket_id)} assigned`);
    }
    case 'manager create': {
      const body: Body = {
        title: v.title,
        goal: v.goal_file != null ? await read_source(inv, v.goal_file) : '',
      };
      if (v.project == null && v.project_id == null)
        fail_validation(inv, 'project required: pass --project or --project-id');
      if (v.project != null) body.project = v.project;
      if (v.project_id != null) body.project_id = v.project_id;
      data = await send(inv, 'POST', '/api/managers', { json_body: body });
      let human = `Created manager ${scalar(data.id)}.`;
      if (data.startup == null) human += ' Idle (no goal).';
      else {
        const startup = data.startup;
        const fate = startup.fate;
        human += ` Startup ${scalar(fate)}.`;
        if (fate === 'queued' && startup.queue_position != null)
          human += ` Queue position: ${scalar(startup.queue_position)}.`;
        if (['refused', 'uncertain', 'failed'].includes(fate)) {
          const reason = startup.refusal_reason || startup.error;
          if (reason) human += ` ${scalar(reason)}`;
          human += `\nThe manager is saved. Inspect: panels manager show ${scalar(data.id)}`;
          human += ['uncertain', 'failed'].includes(fate)
            ? '\nCheck its conversation before any resend. Do not create it again.'
            : `\nRetry on this manager: panels send-message --manager ${scalar(data.id)} --message "<goal>"`;
        }
      }
      return emit(data, as_json, human);
    }
    case 'manager list': {
      if (v.active && v.settled) fail_validation(inv, 'use only one of --active or --settled');
      const params = drop_none({
        settled: v.active ? false : v.settled ? true : null,
        search: v.search,
        project: v.project,
        project_id: v.project_id,
        limit: v.limit,
        offset: v.offset,
      });
      data = await send(inv, 'GET', '/api/managers', { params: { detail: 'summary', ...params } });
      return emit(
        data,
        as_json,
        bounded(
          lines(
            data.managers,
            (row) =>
              `${scalar(row.id)} ${scalar(row.title)} [${scalar(row.settled ? 'settled' : 'active')}]`,
          ),
          data.page,
        ),
      );
    }
    case 'manager show':
      return manager_show(inv);
    case 'manager delete':
      if (!v.yes) fail_validation(inv, 'permanent deletion requires --yes');
      data = await send(inv, 'DELETE', `/api/managers/${v.item_id}`);
      return emit(data, as_json, `${scalar(v.item_id)} permanently deleted`);
    case 'manager set': {
      const value = await value_or_file(inv, v.field);
      if (['title', 'project', 'project-id'].includes(v.field) && value === null)
        fail_validation(inv, `${scalar(v.field)} cannot be cleared`);
      const field = v.field === 'project-id' ? 'project_id' : v.field;
      data = await send(inv, 'PATCH', `/api/managers/${v.item_id}`, {
        json_body: { [field]: value },
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(v.field)} set`);
    }
    case 'manager add-ticket':
    case 'manager remove-ticket': {
      const adding = path === 'manager add-ticket';
      data = await send(
        inv,
        adding ? 'PUT' : 'DELETE',
        `/api/collections/manager_tickets/${v.item_id}/${v.ticket_id}`,
      );
      return emit(
        data,
        as_json,
        adding
          ? `${scalar(v.ticket_id)} added to ${scalar(v.item_id)}`
          : `${scalar(v.ticket_id)} removed from ${scalar(v.item_id)}`,
      );
    }
    case 'manager workspace':
      return manager_workspace(inv);
    case 'manager settle':
      data = await send(inv, 'PATCH', `/api/managers/${v.item_id}/settled`, {
        json_body: { settled: !v.active },
      });
      return emit(data, as_json, `${scalar(v.item_id)} ${scalar(v.active ? 'active' : 'settled')}`);
    case 'manager artifact list': {
      data = await send(inv, 'GET', `/api/managers/${v.item_id}/workspace`);
      const artifacts = artifact_paths(data.artifacts).sort(python_sort);
      return emit({ manager_id: v.item_id, artifacts }, as_json, lines(artifacts, scalar));
    }
    case 'manager artifact read': {
      const content = await send_text(
        inv,
        'GET',
        `/files/managers/${v.item_id}/artifacts/${quote(v.artifact_path, true)}`,
      );
      return as_json
        ? emit({ manager_id: v.item_id, path: v.artifact_path, content }, true, content)
        : raw(content);
    }
    case 'manager artifact write':
      data = await send(
        inv,
        'PUT',
        `/files/managers/${v.item_id}/artifacts/${quote(v.artifact_path, true)}`,
        { json_body: { content: await read_source(inv, v.body_file, true) } },
      );
      return emit(data, as_json, scalar(data.url));
    case 'manager artifact delete':
      data = await send(
        inv,
        'DELETE',
        `/files/managers/${v.item_id}/artifacts/${quote(v.artifact_path, true)}`,
      );
      return emit(data, as_json, `${scalar(v.artifact_path)} deleted`);
    case 'manager conversation send': {
      if ((v.message == null) === (v.body_file == null))
        fail_validation(inv, 'send requires exactly one of --message or --body-file');
      const text = v.message ?? (await read_source(inv, v.body_file ?? ''));
      data = await send(inv, 'POST', `/api/managers/${v.item_id}/conversation/send`, {
        json_body: { content: [{ piece: 'text', text }], sender_label: 'You' },
      });
      return emit(data, as_json, `message ${scalar(data.fate)}`);
    }
    case 'manager conversation reset':
      data = await send(inv, 'POST', `/api/managers/${v.item_id}/conversation/reset`);
      return emit(data, as_json, `${scalar(v.item_id)} Manager conversation reset`);
    case 'worker my-ticket':
      return show_ticket(inv, true);
    case 'worker propose':
      return submit_proposal(inv, v.ticket_id);
    case 'worker recap': {
      if (v.body_file != null)
        fail_validation(
          inv,
          '--body-file is no longer supported: pipe recap text on stdin instead',
        );
      const body = await worker_stdin_body(inv, 'recap');
      const tid = ticket_id(inv, v.ticket_id);
      data = await send(inv, 'PATCH', `/api/tickets/${tid}`, { json_body: { recap: body } });
      return emit(data, as_json, `recap written on ${scalar(data.id)}`);
    }
    default:
      throw new Error(`No work command handler for ${path}`);
  }
}
