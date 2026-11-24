import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { native_style_findings, server_diff } from './native_style.ts';

test('added imports and helper definitions fail while shared owners and service timers pass', () => {
  const fake_name = 'ValueError';
  const retired_name = 'PydanticSerializationError';
  const diff = `+++ b/server/example.ts
@@ -1 +4,22 @@
-import old from 'koffi';
+import ffi from 'koffi';
+} from '../core/python_repr.ts';
+export { python_string_repr } from './other.ts';
+function retry_flock(fd: number) { return flock(fd, 'exclusive'); }
+const bounded = (task: Promise<void>) => task;
+import { python_truth } from './other.ts';
+function python_sum(values: number[]) { return 0; }
+} from '../core/python_repr_unicode.ts';
+this.name = '${fake_name}';
+if (error.name === '${fake_name}') throw error;
+throw Object.assign(new Error('bad'), { name: '${fake_name}' });
+import { pure_posix_path } from '../core/python_path.ts';
+import { strip } from '../core/whitespace.ts';
+import { integer_text } from '../core/python_integer.ts';
+import { ValueError, KeyError, PydanticSerializationError, CalledProcessError } from './old.ts';
+class KeyError extends Error {}
+this.name = '${retired_name}';
+import { JsonNumber, stringify_python_json } from '../core/http/numeric_json.ts';
+class PlannerError extends Error {}
+const note = "import ffi from 'koffi'; function flock() {}";
+function serve() { setTimeout(notify, 10); return Promise.race(tasks); }
+// function deadline() {}
+++ b/server/executor/example.ts
@@ -0,0 +1,3 @@
+import { pure_posix_path } from '../core/python_path.ts';
+import { strip } from '../core/whitespace.ts';
+import { integer_text } from '../core/python_integer.ts';
+++ b/server/execution_identity.ts
@@ -0,0 +1,1 @@
+import { strip } from './core/whitespace.ts';
+++ b/server/core/locks/flock.ts
@@ -0,0 +1,2 @@
+import ffi from 'koffi';
+function retry_flock() {}
`;
  expect(native_style_findings(diff).map(({ path, line }) => [path, line])).toEqual(
    Array.from({ length: 17 }, (_, index) => index + 4).map((line) => ['server/example.ts', line]),
  );
});

test('reads a server diff larger than the 1 MiB child-process default', () => {
  const root = mkdtempSync(join(tmpdir(), 'native-style-diff-'));
  try {
    const git = (...args: string[]) =>
      execFileSync(
        'git',
        ['-c', 'user.name=test', '-c', 'user.email=test@example.invalid', ...args],
        {
          cwd: root,
        },
      );
    git('init', '--quiet');
    mkdirSync(join(root, 'server'));
    writeFileSync(join(root, 'server/large.ts'), '');
    git('add', '.');
    git('commit', '--quiet', '-m', 'base');
    writeFileSync(join(root, 'server/large.ts'), 'export const line = 0;\n'.repeat(80_000));
    const diff = server_diff('HEAD', root);
    expect(Buffer.byteLength(diff)).toBeGreaterThan(1024 * 1024);
    expect(native_style_findings(diff)).toEqual([]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
