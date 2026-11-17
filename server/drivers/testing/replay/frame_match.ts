/** Adapted from T3 Code b79d277cddaf65858418f29d73b87fcc3d1a19c5 (apps/server/scripts/acp-replay-agent.ts).
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/**
 * Frame matching with T3's strength: the same key set at every level, arrays of the same
 * length matched element by element, and exact values.
 *
 * Placeholders:
 * - `<any>`, only as a whole value, matches any value.
 * - `<id:N>` is a value the driver generated, numbered in first-seen order. Only a whole
 *   value binds it, at its first match, and later frames must repeat it. Once bound, it is
 *   also filled in inside a longer string, and an inbound frame carries it back.
 * - A fixed placeholder such as `<workspace>` or `<session>` is substituted wherever it
 *   appears inside a string.
 */
import { is_record } from '../../../core/records.ts';

export const ANY = '<any>';
const NUMBERED = /^<id:\d+>$/;
type Primitive = string | number | boolean | null;

export class ReplayBindings {
  /** Fixed placeholder to its text, such as `<workspace>` to the run's folder. */
  readonly fixed: ReadonlyMap<string, string>;
  readonly #numbered = new Map<string, Primitive>();
  constructor(fixed: Readonly<Record<string, string>>) {
    this.fixed = new Map(Object.entries(fixed));
  }
  substitute(text: string): string {
    let result = text;
    for (const [placeholder, value] of this.fixed) result = result.replaceAll(placeholder, value);
    return result;
  }
  /** A string with its fixed placeholders and the bound string values inside it filled in. */
  materialize(text: string): string {
    return this.substitute(text).replace(/<id:\d+>/g, (placeholder) => {
      const bound = this.#numbered.get(placeholder);
      if (typeof bound !== 'string')
        throw new Error(`${placeholder} inside a string has no bound text`);
      return bound;
    });
  }
  numbered(placeholder: string): Primitive | undefined {
    return this.#numbered.get(placeholder);
  }
  /** Bind several placeholders at once, or none. */
  bind_all(values: ReadonlyMap<string, Primitive>): void {
    for (const [placeholder, value] of values) this.#numbered.set(placeholder, value);
  }
  holder_of(value: Primitive): string | undefined {
    for (const [placeholder, bound] of this.#numbered)
      if (Object.is(bound, value)) return placeholder;
    return undefined;
  }
}

export type FrameMatch =
  { readonly matched: true } | { readonly matched: false; readonly why: string };

function primitive(value: unknown): value is Primitive {
  return (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  );
}
function compare(
  expected: unknown,
  actual: unknown,
  bindings: ReplayBindings,
  pending: Map<string, Primitive>,
  path: string,
): string | null {
  if (expected === ANY) return null;
  if (typeof expected === 'string' && NUMBERED.test(expected)) {
    if (!primitive(actual)) return `${path}: ${expected} needs a plain value`;
    const bound = bindings.numbered(expected) ?? pending.get(expected);
    if (bound !== undefined)
      return Object.is(bound, actual)
        ? null
        : `${path}: ${expected} is ${JSON.stringify(bound)}, not ${JSON.stringify(actual)}`;
    const holder =
      bindings.holder_of(actual) ?? [...pending].find(([, value]) => Object.is(value, actual))?.[0];
    if (holder !== undefined)
      return `${path}: ${JSON.stringify(actual)} is already ${holder}, not ${expected}`;
    pending.set(expected, actual);
    return null;
  }
  if (typeof expected === 'string') {
    let text: string;
    try {
      text = bindings.materialize(expected);
    } catch (error) {
      return `${path}: ${error instanceof Error ? error.message : String(error)}`;
    }
    return actual === text
      ? null
      : `${path}: expected ${JSON.stringify(text)}, got ${JSON.stringify(actual)}`;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual)) return `${path}: expected an array`;
    if (actual.length !== expected.length)
      return `${path}: expected ${expected.length} elements, got ${actual.length}`;
    for (let index = 0; index < expected.length; index += 1) {
      const why = compare(expected[index], actual[index], bindings, pending, `${path}[${index}]`);
      if (why !== null) return why;
    }
    return null;
  }
  if (is_record(expected)) {
    if (!is_record(actual)) return `${path}: expected an object`;
    const expected_keys = Object.keys(expected).sort();
    const actual_keys = Object.keys(actual).sort();
    if (JSON.stringify(expected_keys) !== JSON.stringify(actual_keys))
      return `${path}: keys ${JSON.stringify(actual_keys)}, expected ${JSON.stringify(expected_keys)}`;
    for (const key of expected_keys) {
      const why = compare(expected[key], actual[key], bindings, pending, `${path}.${key}`);
      if (why !== null) return why;
    }
    return null;
  }
  return Object.is(expected, actual)
    ? null
    : `${path}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`;
}

/** Match one frame. A match commits the numbered values it bound. A mismatch binds nothing. */
export function match_frame(
  expected: unknown,
  actual: unknown,
  bindings: ReplayBindings,
): FrameMatch {
  const pending = new Map<string, Primitive>();
  const why = compare(expected, actual, bindings, pending, '$');
  if (why !== null) return { matched: false, why };
  bindings.bind_all(pending);
  return { matched: true };
}

/** The frame the peer writes: bound numbered values and fixed placeholders filled in. */
export function materialize_frame(frame: unknown, bindings: ReplayBindings): unknown {
  if (typeof frame === 'string') {
    if (frame === ANY) throw new Error('an inbound frame cannot hold <any>');
    if (NUMBERED.test(frame)) {
      const bound = bindings.numbered(frame);
      if (bound === undefined) throw new Error(`${frame} has not been bound by an outbound frame`);
      return bound;
    }
    return bindings.materialize(frame);
  }
  if (Array.isArray(frame)) return frame.map((value) => materialize_frame(value, bindings));
  if (is_record(frame))
    return Object.fromEntries(
      Object.entries(frame).map(([key, value]) => [key, materialize_frame(value, bindings)]),
    );
  return frame;
}
