import { expect, test } from 'vitest';
import { native_style_findings } from './native_style.ts';

test('added imports and helper definitions fail while shared owners and service timers pass', () => {
  const diff = `+++ b/server/example.ts
@@ -1 +4,8 @@
-import old from 'koffi';
+import ffi from 'koffi';
+} from '../core/python_repr.ts';
+export { python_string_repr } from './other.ts';
+function retry_flock(fd: number) { return flock(fd, 'exclusive'); }
+const bounded = (task: Promise<void>) => task;
+const note = "import ffi from 'koffi'; function flock() {}";
+function serve() { setTimeout(notify, 10); return Promise.race(tasks); }
+// function deadline() {}
+++ b/server/core/locks/flock.ts
@@ -0,0 +1,2 @@
+import ffi from 'koffi';
+function retry_flock() {}
`;
  expect(native_style_findings(diff).map(({ path, line }) => [path, line])).toEqual(
    [4, 5, 6, 7, 8].map((line) => ['server/example.ts', line]),
  );
});
