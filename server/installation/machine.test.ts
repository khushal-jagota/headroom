import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { test } from 'vitest';
import { PROTOCOL_VERSION } from '../execution_configuration.ts';
import { wait_for_first_machine } from './machine.ts';
import { home_install_fixture } from './testing/home_fixture.ts';

test('first-machine enrollment retries a timed-out observation and still requires a fresh accepted lease', async () => {
  const f = await home_install_fixture();
  try {
    const node = join(f.request.root, 'machine/runtime/node/bin/node');
    const data = join(f.request.root, 'machine/data');
    const started = Date.now();
    const lease = {
      identity: { application_revision: f.request.revision, protocol_version: PROTOCOL_VERSION },
      going_to_sleep: false,
      admission_stopped: false,
      lease_accepted_at_mac_ms: started,
    };
    await mkdir(dirname(node), { recursive: true });
    await mkdir(data, { recursive: true });
    await writeFile(
      node,
      [
        '#!/bin/sh',
        'cd "$PLAN_EXECUTOR_ROOT" || exit 1',
        'echo probe >> attempts',
        'if [ ! -e slow ]; then touch slow; exec /bin/sleep 60; fi',
        'if [ ! -e stale ]; then touch stale; cat old.json; else cat current.json; fi',
      ].join('\n'),
      { mode: 0o700 },
    );
    await writeFile(
      join(data, 'old.json'),
      JSON.stringify({ ...lease, lease_accepted_at_mac_ms: started - 1 }),
    );
    await writeFile(join(data, 'current.json'), JSON.stringify(lease));
    await wait_for_first_machine(f.request, f.deps, f.deps.execution, started);
    assert.equal(await readFile(join(data, 'attempts'), 'utf8'), 'probe\nprobe\nprobe\n');
  } finally {
    await f.close();
  }
}, 15_000);

test('first-machine enrollment exposes a missing executable and honors the enclosing deadline', async () => {
  const f = await home_install_fixture();
  try {
    const wait = (deadline: number | null) =>
      wait_for_first_machine(f.request, f.deps, { ...f.deps.execution, deadline }, Date.now());
    await assert.rejects(wait(null), { code: 'ENOENT' });
    const node = join(f.request.root, 'machine/runtime/node/bin/node');
    await mkdir(dirname(node), { recursive: true });
    await writeFile(node, '#!/bin/sh\nexec /bin/sleep 60\n', { mode: 0o700 });
    await assert.rejects(wait(performance.now() / 1000 + 0.2), /time budget|accepted connection/);
  } finally {
    await f.close();
  }
});
