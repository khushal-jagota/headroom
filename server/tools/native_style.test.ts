import { expect, test } from 'vitest';
import { new_native_style_findings } from './native_style.ts';

test('AST checks reject new imports and helpers without treating text or service timers as code', () => {
  const existing = "import { python_string_repr } from '../core/python_repr.ts';";
  const after = `${existing}
    import ffi from 'koffi';
    export { python_string_repr } from '../core/python_repr.ts';
    const note = "import ffi from 'koffi'; function flock() {}";
    const timer = setTimeout(() => notify(), 10);
    function wait(task: Promise<void>) {
      return Promise.race([task, new Promise(resolve => setTimeout(resolve, 10))]);
    }
    function retry_flock(fd: number) { return native_flock(fd, 2); }
    function deadline() { return AbortSignal.timeout(10); }
  `;
  expect(
    new_native_style_findings('server/example.ts', existing, after).map((finding) => finding.key),
  ).toEqual([
    'koffi-import',
    'python-repr-import',
    'deadline-helper:wait',
    'flock-helper:retry_flock',
    'deadline-helper:deadline',
  ]);
  expect(new_native_style_findings('server/example.ts', after, after)).toEqual([]);
});

test('canonical owners allow their own primitives and loader imports use the same rule', () => {
  expect(
    new_native_style_findings(
      'server/core/locks/flock.ts',
      '',
      `
    import ffi from 'koffi';
    const flock = libc.func('int flock(int fd, int operation)');
    function retry_flock(fd: number) { return flock(fd, 2); }
  `,
    ),
  ).toEqual([]);
  expect(
    new_native_style_findings(
      'server/conversation/shutdown.ts',
      '',
      `
    function observe_shutdown(task: Promise<void>) {
      return Promise.race([task, new Promise(resolve => setTimeout(resolve, 10))]);
    }
  `,
    ),
  ).toEqual([]);
  expect(
    new_native_style_findings(
      'server/example.ts',
      '',
      `
    const ffi = createRequire(import.meta.url)('koffi');
    const representation = import('../core/python_repr.ts');
  `,
    ).map((finding) => finding.key),
  ).toEqual(['koffi-import', 'python-repr-import']);
  const observer = `
    import { setTimeout as delay } from 'node:timers/promises';
    function observe_promise(task: Promise<void>) { return Promise.race([task, delay(10)]); }
  `;
  expect(new_native_style_findings('server/core/promise_observer.ts', '', observer)).toEqual([]);
  expect(
    new_native_style_findings('server/example.ts', '', observer).map((finding) => finding.key),
  ).toEqual(['deadline-helper:observe_promise']);
});
