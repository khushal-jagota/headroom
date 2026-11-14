/** FastAPI-compatible validation for the conversation route bodies. */
import { is_record as object } from '../../core/records.ts';
import { HttpError } from '../../core/http/errors.ts';
export interface Issue {
  type: string;
  loc: (string | number)[];
  msg: string;
  input: unknown;
  ctx?: Record<string, unknown>;
}
// Match the record routes: underscores belong only between integer-part digits.
function integer_text(raw: string): string {
  const point = raw.indexOf('.');
  const whole = point < 0 ? raw : raw.slice(0, point);
  const fraction = point < 0 ? '' : raw.slice(point);
  return whole.replace(/(?<=[0-9])_(?=[0-9])/g, '') + fraction;
}
export class Fields {
  readonly body: Record<string, unknown>;
  readonly issues: Issue[] = [];
  readonly location: (string | number)[];
  constructor(value: unknown, location: (string | number)[] = ['body']) {
    this.location = location;
    this.body = object(value) ? value : {};
    if (!object(value))
      this.issue(
        location.length === 1 && value == null ? 'missing' : 'model_attributes_type',
        [],
        location.length === 1 && value == null
          ? 'Field required'
          : 'Input should be a valid dictionary or object to extract fields from',
        value ?? null,
      );
  }
  issue(
    type: string,
    path: (string | number)[],
    msg: string,
    input: unknown,
    ctx?: Record<string, unknown>,
  ): void {
    this.issues.push({
      type,
      loc: [...this.location, ...path],
      msg,
      input,
      ...(ctx ? { ctx } : {}),
    });
  }
  text(name: string, nullable = false, required = true): string | null {
    if (!(name in this.body)) {
      if (required) this.issue('missing', [name], 'Field required', this.body);
      return null;
    }
    const value = this.body[name];
    if (nullable && value === null) return null;
    if (typeof value !== 'string') {
      this.issue('string_type', [name], 'Input should be a valid string', value);
      return null;
    }
    return value;
  }
  choice(
    name: string,
    choices: readonly string[],
    nullable = false,
    required = true,
  ): string | null {
    if (!(name in this.body)) {
      if (required) this.issue('missing', [name], 'Field required', this.body);
      return null;
    }
    const value = this.body[name];
    if (nullable && value === null) return null;
    if (typeof value === 'string' && choices.includes(value)) return value;
    const expected =
      choices.length === 1
        ? `'${choices[0]}'`
        : choices
            .slice(0, -1)
            .map((v) => `'${v}'`)
            .join(', ') + ` or '${choices.at(-1)}'`;
    this.issue('enum', [name], `Input should be ${expected}`, value, { expected });
    return null;
  }
  integer(name: string, required = true): number | null {
    if (!(name in this.body)) {
      if (required) this.issue('missing', [name], 'Field required', this.body);
      return null;
    }
    const value = this.body[name];
    const converted =
      typeof value === 'boolean'
        ? Number(value)
        : typeof value === 'string' && /^[+-]?[0-9]+(?:\.0+)?$/.test(integer_text(value).trim())
          ? Number(integer_text(value))
          : value;
    if (typeof converted === 'number' && Number.isFinite(converted) && Number.isInteger(converted))
      return converted;
    const type =
      typeof value === 'string'
        ? 'int_parsing'
        : typeof value === 'number'
          ? 'int_from_float'
          : 'int_type';
    const msg =
      type === 'int_parsing'
        ? 'Input should be a valid integer, unable to parse string as an integer'
        : type === 'int_from_float'
          ? 'Input should be a valid integer, got a number with a fractional part'
          : 'Input should be a valid integer';
    this.issue(type, [name], msg, value);
    return null;
  }
  extras(allowed: readonly string[]): void {
    for (const [key, value] of Object.entries(this.body))
      if (!allowed.includes(key))
        this.issue('extra_forbidden', [key], 'Extra inputs are not permitted', value);
  }
  finish(): void {
    if (this.issues.length) throw new HttpError(422, this.issues);
  }
}
export function body_fields(value: unknown): Fields {
  const fields = new Fields(value);
  // A failed top-level body does not also report every missing member.
  fields.finish();
  return fields;
}

export { is_record as object } from '../../core/records.ts';
