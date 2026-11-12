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
            'nested_score=$(/bin/bash -c \'read -r score < /proc/self/oom_score_adj; printf "%s\\n" "$score"\'); ' +
            'printf "%s\\n" "$nested_score"; ' +
            'if [[ "$score" != 1000 || "$nested_score" != 1000 ]]; then ' +
            'readable=no; if [[ -r "$BASH_ENV" ]]; then readable=yes; fi; ' +
            'printf "BASH_ENV=%q readable=%s uid=%s euid=%s\\n" "$BASH_ENV" "$readable" "$UID" "$EUID" >&2; ' +
            'printf "bash_version=%q flags=%q shellopts=%q\\n" "$BASH_VERSION" "$-" "$SHELLOPTS" >&2; ' +
            'type echo >&2; ' +
            'source "$BASH_ENV"; source_status=$?; ' +
            'read -r after_source < /proc/self/oom_score_adj; ' +
            'printf "source_status=%s score_after_source=%s\\n" "$source_status" "$after_source" >&2; ' +
            'printf "1000\\n" > /proc/self/oom_score_adj; write_status=$?; ' +
            'read -r after_write < /proc/self/oom_score_adj; ' +
            'printf "self_write_status=%s score_after_write=%s\\n" "$write_status" "$after_write" >&2; ' +
            'fi',
        ],
        { cwd: directory, env: environment, encoding: 'utf8', timeout: 5000 },
      );
      expect(child.error).toBeUndefined();
      expect(child.status).toBe(0);
      expect(
        child.stdout,
        child.stderr +
          JSON.stringify({
            uid: process.getuid!(),
            euid: process.geteuid!(),
            gid: process.getgid!(),
            egid: process.getegid!(),
          }),
      ).toBe('1000\n1000\n');
      expect(readFileSync('/proc/self/oom_score_adj', 'utf8')).toBe(before);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  },
);
