/** Goal CLI handlers keep requests and output separate from server-domain behavior. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { emit, fail_validation, json, send, send_text } from '../../cli/http.ts';
import { read_source, stdin_object } from '../../cli/input.ts';
import { numeric_value } from '../../core/http/numeric_json.ts';
import { scalar_text } from '../../cli/text.ts';
import { response_field } from '../../cli/response_field.ts';

const quote = (value: string, slash = false): string => {
  const encoded = encodeURIComponent(value).replace(
    /[!'()*]/g,
    (character) => '%' + character.charCodeAt(0).toString(16).toUpperCase(),
  );
  return slash ? encoded.replace(/%2F/g, '/') : encoded;
};
const artifact_url = (goal_id: string, path: string): string =>
  `/files/goals/${quote(goal_id)}/artifacts/${quote(path, true)}`;
const goal_response = (data: unknown, as_json: boolean): CommandResult =>
  emit(data, as_json, json(data, { ensure_ascii: false, indent: 2 }));
async function list_artifacts(invocation: Invocation): Promise<CommandResult> {
  const goal_id = invocation.values.goal_id;
  const directories = [''];
  const paths = new Set<string>();
  while (directories.length) {
    const directory = directories.pop()!;
    let offset = 0;
    while (true) {
      const page = await send(invocation, 'GET', `/api/artifact-catalog/goal/${quote(goal_id)}`, {
        params: { directory, offset },
      });
      const entries = response_field(invocation, page, 'entries');
      for (const entry of entries) {
        const href = response_field(invocation, entry, 'href');
        if (href) {
          const path = response_field(invocation, entry, 'path');
          if (path.startsWith('artifacts/')) paths.add(path.slice(10));
        }
        const child = response_field(invocation, entry, 'directory');
        if (child && (child === 'artifacts' || child.startsWith('artifacts/')))
          directories.push(child);
      }
      offset += entries.length;
      if (offset >= numeric_value(response_field(invocation, page, 'total'))! || !entries.length)
        break;
    }
  }
  const artifacts = [...paths].sort();
  return emit({ goal_id, artifacts }, invocation.values.as_json, artifacts.join('\n'));
}

export async function run_goal_command(invocation: Invocation): Promise<CommandResult> {
  const values = invocation.values;
  const as_json = values.as_json;
  const path = invocation.command.path.join(' ');
  switch (path) {
    case 'goal schema': {
      const { default: goal_guidance } = await import('./guidance.json', {
        with: { type: 'json' },
      });
      return goal_response(goal_guidance[values.operation as keyof typeof goal_guidance], as_json);
    }
    case 'goal list': {
      const params: Record<string, unknown> = {};
      if (values.active != null) params.active = String(values.active).toLowerCase();
      if (values.project_id != null) params.project_id = values.project_id;
      return goal_response(await send(invocation, 'GET', '/api/goals', { params }), as_json);
    }
    case 'goal show':
      return goal_response(
        await send(invocation, 'GET', `/api/goals/${quote(values.goal_id)}`),
        as_json,
      );
    case 'goal create':
      return goal_response(
        await send(invocation, 'POST', '/api/goals', {
          json_body: await stdin_object(invocation),
        }),
        as_json,
      );
    case 'goal edit':
      return goal_response(
        await send(invocation, 'PATCH', `/api/goals/${quote(values.goal_id)}`, {
          json_body: await stdin_object(invocation),
        }),
        as_json,
      );
    case 'goal read': {
      const params: Record<string, unknown> = {};
      if (values.first_date != null) params.first_date = values.first_date;
      if (values.last_date != null) params.last_date = values.last_date;
      return goal_response(
        await send(invocation, 'GET', `/api/goals/${quote(values.goal_id)}/readings`, { params }),
        as_json,
      );
    }
    case 'goal artifact list':
      return list_artifacts(invocation);
    case 'goal artifact read': {
      const content = await send_text(
        invocation,
        'GET',
        artifact_url(values.goal_id, values.artifact_path),
      );
      return as_json
        ? emit({ goal_id: values.goal_id, path: values.artifact_path, content }, true, content)
        : { exit_code: 0, stdout: content, stderr: '' };
    }
    case 'goal artifact write': {
      const data = await send(
        invocation,
        'PUT',
        artifact_url(values.goal_id, values.artifact_path),
        {
          json_body: { content: await read_source(invocation, values.body_file, true) },
        },
      );
      return emit(data, as_json, scalar_text(response_field(invocation, data, 'url')));
    }
    case 'goal artifact delete': {
      const data = await send(
        invocation,
        'DELETE',
        artifact_url(values.goal_id, values.artifact_path),
      );
      return emit(data, as_json, `${scalar_text(values.artifact_path)} deleted`);
    }
    case 'goal make-manager':
      return goal_response(
        await send(invocation, 'POST', `/api/goals/${quote(values.goal_id)}/make-manager`, {
          json_body: await stdin_object(invocation),
        }),
        as_json,
      );
    case 'goal delete': {
      if (!values.yes) fail_validation(invocation, 'permanent deletion requires --yes');
      const data = await send(invocation, 'DELETE', `/api/goals/${quote(values.goal_id)}`);
      return emit(data, as_json, `${scalar_text(values.goal_id)} permanently deleted`);
    }
    default:
      throw new Error(`No goal command handler for ${path}`);
  }
}
