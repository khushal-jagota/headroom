import {
  JsonNumber,
  clone_numeric_json,
  numeric_value,
  numeric_kind,
} from '../../core/http/numeric_json.ts';
/** Plain page-kit validation; chart preparation remains the goal unit's responsibility. */
import type { ManagerPageDocument as PageDocument } from '../contracts.ts';
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): value is ObjectValue =>
  value !== null &&
  typeof value === 'object' &&
  !Array.isArray(value) &&
  !(value instanceof JsonNumber);
const fail = (message: string): never => {
  throw new TypeError(message);
};
type Schema = string | { [key: string]: Schema } | readonly Schema[];
const tone = 'neutral|attention|bad|good';
const definitions: Record<string, Record<string, Schema>> = {
  text: { type: '=text', id: 'string', text: 'string' },
  tickets: { type: '=tickets', id: 'string' },
  checklist: {
    type: '=checklist',
    id: 'string',
    items: [
      {
        id: 'string',
        label: 'string',
        state: 'done|open|blocked',
        'detail?': 'string',
        'action?': 'string',
        'deadline?': 'nullable-string',
      },
    ],
    'title?': 'string',
    'doneTitle?': 'string',
  },
  table: {
    type: '=table',
    id: 'string',
    columns: ['string'],
    rows: [['string']],
    'cell_tones?': [['nullable-tone']],
    'caption?': 'string',
    'empty_message?': 'string',
    'column_widths?': ['number'],
  },
  section: {
    type: '=section',
    id: 'string',
    title: 'string',
    'summary?': 'string',
    'blocks?': ['block'],
    'views?': [{ id: 'string', label: 'string', blocks: ['block'] }],
  },
  grid: {
    type: '=grid',
    id: 'string',
    blocks: ['block'],
    'columns?': '2|3|4',
    'joined?': 'boolean',
    'align?': 'stretch|start',
  },
  segmented: {
    type: '=segmented',
    id: 'string',
    views: [{ id: 'string', label: 'string', blocks: ['block'] }],
  },
  callout: { type: '=callout', id: 'string', tone, text: 'string', 'title?': 'string' },
  metrics: {
    type: '=metrics',
    id: 'string',
    items: [
      { label: 'string', value: 'string', 'hint?': 'string', 'change?': 'string', 'tone?': tone },
    ],
  },
  properties: { type: '=properties', id: 'string', items: [{ label: 'string', value: 'string' }] },
  status: { type: '=status', id: 'string', label: 'string', tone },
  rows: {
    type: '=rows',
    id: 'string',
    items: [
      {
        title: 'string',
        'detail?': 'string',
        'meta?': 'string',
        'status?': 'string',
        'tone?': tone,
      },
    ],
  },
};
function validate(value: unknown, schema: Schema, path: string): void {
  if (Array.isArray(schema)) {
    if (!Array.isArray(value)) fail(`${path}: expected a list`);
    (value as unknown[]).forEach((item, index) => validate(item, schema[0]!, `${path}[${index}]`));
    return;
  }
  if (typeof schema !== 'string') {
    if (!object(value)) fail(`${path}: expected an object`);
    const fields = schema as Record<string, Schema>;
    const allowed = Object.keys(fields).map((key) => key.replace(/\?$/, ''));
    const unknown = Object.keys(value as ObjectValue)
      .filter((key) => !allowed.includes(key))
      .sort()[0];
    if (unknown !== undefined)
      fail(
        /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(unknown)
          ? `${path}.${unknown}: unknown field`
          : `${path}: unknown field`,
      );
    for (const [raw, kind] of Object.entries(fields)) {
      const key = raw.replace(/\?$/, '');
      if (!(key in (value as ObjectValue))) {
        if (raw.endsWith('?')) continue;
        fail(`${path}.${key}: required`);
      }
      validate((value as ObjectValue)[key], kind, `${path}.${key}`);
    }
    return;
  }
  if (schema === 'block') {
    if (!object(value) || typeof value.type !== 'string' || !definitions[value.type])
      fail(`${path}: invalid value or block shape`);
    validate(value, definitions[(value as ObjectValue).type as string]!, path);
  } else if (schema === 'nullable-string') {
    if (value !== null && typeof value !== 'string') fail(`${path}: invalid value or block shape`);
  } else if (schema === 'nullable-tone') {
    if (
      value !== null &&
      (typeof value !== 'string' ||
        !['neutral', 'attention', 'bad', 'good', 'info'].includes(value))
    )
      fail(`${path}: invalid value or block shape`);
  } else if (schema.startsWith('=') || schema.includes('|')) {
    const values = schema.startsWith('=') ? [schema.slice(1)] : schema.split('|');
    if (
      !values.some((option) =>
        /^[0-9]+$/.test(option)
          ? numeric_kind(value) === 'integer' && numeric_value(value) === Number(option)
          : value === option,
      )
    )
      fail(
        `${path}: unsupported value; supported values: ${values.map((item) => (/^[0-9]+$/.test(item) ? item : `'${item}'`)).join(', ')}`,
      );
  } else if (schema === 'number') {
    if (numeric_value(value) === undefined || !Number.isFinite(numeric_value(value)))
      fail(`${path}: expected a finite number`);
  } else if (typeof value !== schema) fail(`${path}: unexpected value type`);
}
export function has_chart(value: unknown): boolean {
  if (!object(value)) return false;
  if (value.type === 'chart') return true;
  return (
    (Array.isArray(value.blocks) && value.blocks.some(has_chart)) ||
    (Array.isArray(value.views) && value.views.some(has_chart))
  );
}
export function prepare_plain_document(value: unknown, identity: () => string): PageDocument {
  const document = clone_numeric_json(value);
  if (!object(document) || !Array.isArray(document.blocks))
    fail('page: expected a page-kit document');
  const assign = (items: unknown[]): void => {
    for (const item of items) {
      if (!object(item)) continue;
      if (!('id' in item)) item.id = identity();
      if (item.type === 'checklist' && Array.isArray(item.items)) {
        for (const check of item.items)
          if (object(check) && !('id' in check)) check.id = identity();
      }
      if (Array.isArray(item.blocks)) assign(item.blocks);
      if (Array.isArray(item.views))
        for (const view of item.views)
          if (object(view) && Array.isArray(view.blocks)) assign(view.blocks);
    }
  };
  assign((document as ObjectValue).blocks as unknown[]);
  validate(
    document,
    {
      kind: '=headroom.page',
      version: '=1',
      title: 'string',
      summary: 'string',
      blocks: ['block'],
    },
    '$',
  );
  const identities = new Set<string>();
  const visit = (items: ObjectValue[]): void => {
    for (const block of items) {
      const id = block.id as string;
      if (!id.trim() || identities.has(id)) fail('page: component IDs must be nonempty and unique');
      identities.add(id);
      if (block.type === 'checklist')
        for (const item of block.items as ObjectValue[]) {
          const check = item.id as string;
          if (!check.trim() || identities.has(check))
            fail('page: checklist IDs must be nonempty and unique');
          identities.add(check);
          if (
            item.deadline !== undefined &&
            item.deadline !== null &&
            (!/^\d{4}-\d{2}-\d{2}$/.test(item.deadline as string) ||
              !Number.isFinite(new Date(item.deadline as string).getTime()) ||
              new Date(item.deadline as string).toISOString().slice(0, 10) !== item.deadline)
          )
            fail('checklist item deadline must be an ISO calendar date or null');
        }
      if (block.type === 'section' && 'blocks' in block === 'views' in block)
        fail('section needs either blocks or views');
      if (block.type === 'table') {
        const columns = block.columns as unknown[];
        const rows = block.rows as unknown[][];
        if (rows.some((row) => row.length !== columns.length))
          fail('table row must match its columns');
        if ('cell_tones' in block) {
          const tones = block.cell_tones as unknown[][];
          if (tones.length !== rows.length || tones.some((row) => row.length !== columns.length))
            fail('table cell tones must match its rows and columns');
        }
        if ('column_widths' in block) {
          const widths = (block.column_widths as unknown[]).map((value) => numeric_value(value)!);
          if (
            widths.length !== columns.length ||
            widths.some((width) => width <= 0) ||
            Math.abs(widths.reduce((a, b) => a + b, 0) - 100) > 1e-6
          )
            fail('table column widths must be positive percentages totaling 100');
        }
      }
      if (Array.isArray(block.blocks)) visit(block.blocks as ObjectValue[]);
      if (Array.isArray(block.views))
        for (const view of block.views as ObjectValue[]) visit(view.blocks as ObjectValue[]);
    }
  };
  visit((document as ObjectValue).blocks as ObjectValue[]);
  return document as unknown as PageDocument;
}
