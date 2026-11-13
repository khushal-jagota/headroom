/** Executor validation diagnostics and semantic class tags. */
import { inspect } from 'node:util';
import { ValueError } from '../core/value_error.ts';
import { native_class_name } from './protocol_classes.ts';
export { ValueError } from '../core/value_error.ts';
// The Mac runner still imports this name until its diagnostic migration lands.
export { inspect as python_repr } from 'node:util';
export interface Issue {
  type: string;
  loc: readonly (string | number)[];
  message: string;
  input: unknown;
}
const classes = new WeakMap<object, string>();
const tuples = new WeakSet<object>();
export function class_name(value: unknown): string | undefined {
  return value !== null && typeof value === 'object'
    ? (classes.get(value) ?? native_class_name(value))
    : undefined;
}
export function tag_class(value: object, name: string): void {
  classes.set(value, name);
}
export function tag_tuple(value: object): void {
  tuples.add(value);
}
export function tuple_value(value: unknown): boolean {
  return value !== null && typeof value === 'object' && tuples.has(value);
}
export class ValidationError extends ValueError {
  readonly issues: readonly Issue[];
  constructor(root: string, issues: readonly Issue[]) {
    super(
      `Invalid ${root}: ` +
        issues.map((issue) => `${issue.loc.join('.') || 'value'}: ${issue.message}`).join('; '),
    );
    this.name = 'ValidationError';
    this.issues = issues;
  }
}
export class KeyError extends Error {
  constructor(key: unknown) {
    super(`Missing executor field ${inspect(key)}`);
    this.name = 'KeyError';
  }
}
export class PydanticSerializationError extends ValueError {
  constructor(message: string) {
    super(message);
    this.name = 'PydanticSerializationError';
  }
}
