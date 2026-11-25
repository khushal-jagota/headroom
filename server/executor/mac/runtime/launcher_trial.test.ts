import { expect, test } from 'vitest';
import { mac_designated_requirement } from './launcher_trial.ts';

test('preserves explicit and implied macOS signing requirements', () => {
  const hash = 'a'.repeat(40),
    cdhash = 'cdhash H"' + hash + '"';
  const identity = 'identifier "com.panels.executor.typescript.app" and anchor apple generic';
  expect(mac_designated_requirement('Executable=/a\ndesignated => ' + identity)).toBe(identity);
  expect(mac_designated_requirement('Executable=/a\n# designated => ' + cdhash)).toBe(cdhash);
  expect(
    mac_designated_requirement('# designated => ' + cdhash + '\ndesignated => ' + identity),
  ).toBe(identity);
  expect(mac_designated_requirement('Signature=adhoc\nCDHash=' + hash)).toBe(cdhash);
  expect(() => mac_designated_requirement('TeamIdentifier=FB99BBDJBA\nCDHash=' + hash)).toThrow();
  expect(() => mac_designated_requirement('Signature=adhoc\nCDHash=invalid')).toThrow();
});
