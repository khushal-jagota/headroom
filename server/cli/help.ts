import type { CommandDefinition, Parameter } from './contracts.ts';
import { command_path } from './errors.ts';

function wrap(text: string, width: number, initial = '', subsequent = initial): string {
  const chunks =
    text
      .trim()
      .replace(/[\t\n\r\f\v]/g, ' ')
      .match(/\s+|[^\s]+/g) ?? [];
  const lines: string[] = [];
  let line = initial;
  let whitespace = '';
  for (let word of chunks) {
    if (/^\s+$/.test(word)) {
      whitespace = word;
      continue;
    }
    let prefix = lines.length === 0 ? initial : subsequent;
    if (line.length > prefix.length && line.length + whitespace.length + word.length > width) {
      lines.push(line.trimEnd());
      line = subsequent;
      whitespace = '';
      prefix = subsequent;
    }
    if (line.length > prefix.length) line += whitespace;
    whitespace = '';
    while (word.length > width - line.length) {
      const remaining = width - line.length;
      if (remaining <= 0) {
        lines.push(line.trimEnd());
        line = subsequent;
        continue;
      }
      line += word.slice(0, remaining);
      word = word.slice(remaining);
      lines.push(line);
      line = subsequent;
    }
    line += word;
  }
  if (line.trim()) lines.push(line.trimEnd());
  return lines.join('\n');
}
function paragraphs(text: string, indent = '  ', width = 78): string {
  return text
    .trim()
    .split(/\n\s*\n/)
    .map((p) => {
      if (p.startsWith('\b'))
        return p
          .slice(1)
          .trim()
          .split('\n')
          .map((s) => indent + s)
          .join('\n');
      return wrap(p, width, indent);
    })
    .join('\n\n');
}
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
function definitions(rows: [string, string][], width = 78): string {
  const first = Math.min(Math.max(...rows.map((r) => r[0].length)), 30) + 2;
  return rows
    .map(([name, description]) => {
      if (!description) return '  ' + name;
      if (name.length + 2 > first)
        return '  ' + name + '\n' + paragraphs(description, ' '.repeat(first + 2), width);
      return wrap(description, width, '  ' + name.padEnd(first), ' '.repeat(first + 2));
    })
    .join('\n');
}
function short_help(help: string, limit: number): string {
  const words = help
    .split(/\n\s*\n/, 1)[0]
    .trim()
    .split(/\s+/);
  let result = '';
  for (const word of words) {
    if ((result ? result.length + 1 : 0) + word.length > limit) {
      while (result.length + 3 > limit && result.includes(' '))
        result = result.slice(0, result.lastIndexOf(' '));
      return result + '...';
    }
    result += (result ? ' ' : '') + word;
    if (word.endsWith('.')) break;
  }
  return result;
}
export function render_help(
  command: CommandDefinition,
  all: readonly CommandDefinition[],
  program = 'panels',
): string {
  const path = command_path(command).replace(/^panels/, program);
  const usage = [
    path,
    '[OPTIONS]',
    ...command.parameters.filter((p) => p.argument).map((p) => p.metavar),
    ...(command.group ? ['COMMAND [ARGS]...'] : []),
  ].join(' ');
  const lines = [wrap(usage, 78, 'Usage: ', '       ')];
  if (command.help.trim()) lines.push('', paragraphs(command.help));
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
      const limit = 78 - 6 - Math.max(...children.map((c) => c.path.at(-1)!.length));
      lines.push(
        '',
        'Commands:',
        definitions(children.map((c) => [c.path.at(-1)!, short_help(c.help, limit)])),
      );
    }
  }
  return lines.join('\n') + '\n';
}
