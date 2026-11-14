import type { CommandDefinition, Parameter } from './contracts.ts';
import { command_path } from './errors.ts';

function option_name(p: Parameter): string {
  let result = (p.opts ?? []).join(', ');
  if (p.secondary_opts?.length) result += ' / ' + p.secondary_opts.join(', ');
  if (!p.flag) result += ' ' + (p.metavar ?? 'TEXT');
  return result;
}
function option_help(p: Parameter): string {
  let help = p.help ?? '';
  const extras = [];
  if (p.show_default && p.default !== null && p.default !== undefined) {
    let value = Array.isArray(p.default) ? p.default.join(', ') : String(p.default);
    if (p.flag && p.default === false && !p.secondary_opts?.length) value = '';
    if (typeof p.show_default === 'string') value = '(' + p.show_default + ')';
    if (value) extras.push('default: ' + value);
  }
  if (p.min !== undefined || p.max !== undefined) {
    const name = p.type === 'float' ? 'x' : 'x';
    extras.push(
      p.min !== undefined && p.max !== undefined
        ? `${p.min}<=${name}<=${p.max}`
        : p.min !== undefined
          ? `${name}>=${p.min}`
          : `${name}<=${p.max}`,
    );
  }
  if (p.required) extras.push('required');
  if (extras.length) help += (help ? '  ' : '') + '[' + extras.join('; ') + ']';
  return help;
}
function definitions(rows: [string, string][]): string {
  return rows
    .map(([name, description]) => `  ${name}${description ? '\n    ' + description : ''}`)
    .join('\n');
}
export function render_help(
  command: CommandDefinition,
  all: readonly CommandDefinition[],
  program = 'panels',
): string {
  const path = command_path(command).replace(/^panels/, program);
  const args = [
    '[OPTIONS]',
    ...command.parameters.filter((p) => p.argument).map((p) => p.metavar),
    ...(command.group ? ['COMMAND [ARGS]...'] : []),
  ].join(' ');
  const lines = [`Usage: ${path} ${args}`];
  if (command.help.trim()) lines.push('', command.help.replace(/\u0008/g, '').trim());
  const options: [string, string][] = command.parameters
    .filter((p) => !p.argument && !p.hidden)
    .map((p) => [option_name(p), option_help(p)]);
  options.push(['--help', 'Show this message and exit.']);
  lines.push('', 'Options:', definitions(options));
  if (command.group) {
    const children = all
      .filter(
        (c) =>
          c.path.length === command.path.length + 1 &&
          c.path.slice(0, -1).join(' ') === command.path.join(' ') &&
          !c.hidden,
      )
      .sort((a, b) => (a.path.at(-1)! < b.path.at(-1)! ? -1 : 1));
    if (children.length) {
      lines.push(
        '',
        'Commands:',
        definitions(children.map((c) => [c.path.at(-1)!, c.help.trim().split('\n')[0]])),
      );
    }
  }
  return lines.join('\n') + '\n';
}
