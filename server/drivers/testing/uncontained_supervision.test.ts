/**
 * The launch bootstrap creates its own cgroup on Linux even under uncontained supervision,
 * and the parent is the only one left to remove it. Without that, every replay launch leaves
 * one empty `panels-worker-*` group behind.
 */
import { accessSync, constants, existsSync, readFileSync } from 'node:fs';
import { readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import * as records from '../../worker_supervision/records.ts';
import { drive_driver, prepare_replay } from './replay/harness.ts';
import { REPLAY_TEST_TIMEOUT_MS } from './replay/rules.ts';
import { TWO_TURNS } from './replay/scenarios.ts';
import { transcript_paths } from './replay/transcript.ts';

/** Whether a launch here can create and join a group under this process's own cgroup. */
function a_launch_here_joins_a_cgroup(): boolean {
  if (process.platform !== 'linux') return false;
  try {
    const membership = readFileSync('/proc/self/cgroup', 'utf8')
      .split('\n')
      .find((line) => line.startsWith('0::'));
    // A group directly under the hierarchy root is refused custody, so it cannot be joined.
    const relative = membership?.slice(3).replace(/^\/+/, '');
    if (!relative) return false;
    const parent = join('/sys/fs/cgroup', relative);
    accessSync(parent, constants.W_OK);
    return readFileSync(join(parent, 'cgroup.subtree_control'), 'utf8').trim() === '';
  } catch {
    return false;
  }
}

test.skipIf(!a_launch_here_joins_a_cgroup())(
  'an uncontained launch removes the cgroup its bootstrap created',
  async () => {
    const prepared = await prepare_replay(transcript_paths('two_turns', 'claude')[0]!, TWO_TURNS);
    try {
      await drive_driver({
        driver_kind: prepared.transcript.driver_kind,
        model: prepared.transcript.model,
        executable: prepared.executable,
        folders: prepared.folders,
        scenario: TWO_TURNS,
      });
      const names = (await readdir(prepared.folders.records)).filter((name) =>
        /^[0-9a-f]{32}\.json$/.test(name),
      );
      expect(names).toHaveLength(1);
      const record = await records.read(join(prepared.folders.records, names[0]!));
      // The bootstrap did create a group, so its absence is the cleanup.
      expect(record.cgroup_joined).toBe(true);
      expect(record.state).toBe('complete');
      expect(existsSync(record.cgroup!.path)).toBe(false);
    } finally {
      await rm(prepared.folders.root, { recursive: true, force: true });
    }
  },
  REPLAY_TEST_TIMEOUT_MS,
);
