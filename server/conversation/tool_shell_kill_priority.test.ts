/** Actual owned shells prove BASH_ENV inheritance; no service manager is involved. */
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { expect, it } from 'vitest';
import { with_tool_shell_kill_priority } from './tool_shell_kill_priority.ts';

it.skipIf(process.platform !== 'linux')(
  'raises owned tool descendants without changing the agent score',
  () => {
    const before = readFileSync('/proc/self/oom_score_adj', 'utf8');
    const directory = mkdtempSync(`${tmpdir()}/tool-shell-`);
    try {
      const environment = with_tool_shell_kill_priority({
        HOME: directory,
        PATH: '/usr/bin:/bin',
      });
      const child = spawnSync(
        '/bin/bash',
        [
          '-c',
          'read -r score < /proc/self/oom_score_adj; printf "%s\\n" "$score"; ' +
            '/bin/bash -c \'read -r score < /proc/self/oom_score_adj; printf "%s\\n" "$score"\'',
        ],
        { cwd: directory, env: environment, encoding: 'utf8', timeout: 5000 },
      );
      expect(child.error).toBeUndefined();
      expect(child.status).toBe(0);
      expect(child.stdout).toBe('1000\n1000\n');
      expect(readFileSync('/proc/self/oom_score_adj', 'utf8')).toBe(before);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  },
);
