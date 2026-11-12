import { python_string_repr as shared_string_repr } from '../../core/python_repr.ts';
import { WorkerUnitError } from './contracts.ts';

export function properties(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const separator = line.indexOf('=');
    if (separator >= 0) result[line.slice(0, separator)] = line.slice(separator + 1);
  }
  return result;
}

const python_string_repr = shared_string_repr;

/** Quote EnvironmentFile values without shell or systemd variable expansion. */
export function environment_file(environment: Readonly<Record<string, string>>): string {
  const lines: string[] = [];
  for (const [name, value] of Object.entries(environment)) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*(?![\s\S])/.test(name) || value.includes('\0')) {
      throw new WorkerUnitError(
        `Invalid environment entry for Worker: ${python_string_repr(name)}`,
      );
    }
    const escaped = value
      .replaceAll('\\', '\\\\')
      .replaceAll('"', '\\"')
      .replaceAll('$', '\\$')
      .replaceAll('`', '\\`');
    lines.push(`${name}="${escaped}"\n`);
  }
  return lines.join('');
}

/** Python pathlib preserves parent segments and executable symlinks. */
export function absolute_path(value: string, directory: string): string {
  const input = value.startsWith('/') ? value : `${directory}/${value}`;
  const prefix = input.startsWith('//') && !input.startsWith('///') ? '//' : '/';
  return (
    prefix +
    input
      .split('/')
      .filter((part) => part !== '' && part !== '.')
      .join('/')
  );
}

export function shell_quote(value: string): string {
  if (!value) return "''";
  if (/^[\w@%+=:,./-]+$/.test(value)) return value;
  return `'${value.replaceAll("'", "'\"'\"'")}'`;
}

export function integer_weight(value: string | undefined): number {
  if (!value || !/^[+-]?\d+$/.test(value.trim())) return 0;
  return Number(value);
}
