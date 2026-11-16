import { expect, test } from 'vitest';
import { hermes_source_root } from './launch.ts';

test('source root skips dot components without cancelling parent components', () => {
  expect(hermes_source_root('/opt/link/../hermes/venv/bin/./python')).toBe('/opt/link/../hermes');
});
