import { stringify_python_json } from '../core/http/numeric_json.ts';
export interface RecordPart {
  value: any;
  proposal: any;
  approval?: Record<string, any>;
}
export function part(
  value: any,
  options: { proposal?: any; approval?: Record<string, any> | null } = {},
): RecordPart {
  const result: RecordPart = { value, proposal: options.proposal ?? null };
  if (options.approval !== undefined && options.approval !== null)
    result.approval = options.approval;
  return result;
}
export function parse_part_names(raw: string | null): string[] | null {
  if (raw === null || raw === undefined) return null;
  const names = raw.split(',').map((s) => s.trim());
  if (!names.length || names.some((s) => !s))
    throw new Error('part names must be a comma-separated list');
  return names;
}
const length = (text: string): number => [...text].length;
export function project_record(
  header: Record<string, any>,
  parts: Record<string, RecordPart>,
  requested: string[] | null,
): any {
  if (requested === null) {
    const manifest: Record<string, any> = {};
    for (const [name, p] of Object.entries(parts)) {
      manifest[name] = {
        character_count:
          p.value === null
            ? 0
            : typeof p.value === 'string'
              ? length(p.value)
              : length(stringify_python_json(p.value)),
      };
      if ('approval' in p) manifest[name].approval = p.approval;
    }
    return { header: { ...header }, manifest };
  }
  const duplicates = [
    ...new Set(requested.filter((s) => requested.filter((n) => n === s).length > 1)),
  ];
  const valid = Object.keys(parts).join(', ');
  if (duplicates.length)
    throw new Error(`duplicate part names: ${duplicates.join(', ')}; valid part names: ${valid}`);
  const unknown = requested.filter((s) => !(s in parts));
  if (unknown.length)
    throw new Error(`unknown part names: ${unknown.join(', ')}; valid part names: ${valid}`);
  return { header: { ...header }, parts: Object.fromEntries(requested.map((s) => [s, parts[s]])) };
}
function display(value: any): string {
  if (value === null) return 'null';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return value;
  return stringify_python_json(value, { sort_keys: true });
}
function append(lines: string[], label: string, value: any, indent: string, annotation = ''): void {
  const rendered = display(value);
  if (!rendered.includes('\n')) {
    lines.push(`${indent}${label}: ${rendered}${annotation}`);
    return;
  }
  lines.push(`${indent}${label}: |${annotation}`);
  const split = rendered.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/);
  if (split.at(-1) === '') split.pop();
  lines.push(...split.map((s) => `${indent}  ${s}`));
}
export function render_text(projection: any): string {
  const lines = ['header:'];
  for (const [key, value] of Object.entries(projection.header)) append(lines, key, value, '  ');
  if (projection.manifest !== undefined) {
    lines.push('', 'manifest:');
    for (const [name, entry] of Object.entries(projection.manifest) as [string, any][]) {
      lines.push(
        `  ${name}:${entry.approval ? ' # ' + entry.approval.display_label : ''}`,
        `    character_count: ${stringify_python_json(entry.character_count)}`,
      );
    }
  } else {
    lines.push('', 'parts:');
    for (const [name, p] of Object.entries(projection.parts) as [string, RecordPart][]) {
      lines.push(`  ${name}:`);
      append(lines, 'value', p.value, '    ', p.approval ? ' # ' + p.approval.display_label : '');
      append(lines, 'proposal', p.proposal, '    ');
    }
  }
  return lines.join('\n');
}
