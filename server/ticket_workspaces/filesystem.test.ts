import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import {
  mkdtempSync,
  mkdirSync,
  realpathSync,
  lstatSync,
  existsSync,
  writeFileSync,
  readFileSync,
  symlinkSync,
  rmSync,
  renameSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import { BackendCommands } from '../conversation/snapshot/commands.ts';
import {
  open_directory_descriptor,
  close_native_descriptor,
  stat_darwin_entry,
} from '../executor/mac/platform.ts';
import { join, dirname, resolve, sep } from 'node:path';
import { path_is_within } from '../core/path_containment.ts';
import {
  retry_retired_workspaces,
  cleanup_ticket_folder,
  cleanup_local_workspaces,
  prepare_workspace_retirement,
  read_workspace_identity,
  reason_to_keep,
  workspace_identity,
  prepare_workspace,
} from './filesystem.ts';
import { git, ticket_match } from './filesystem/shared.ts';
import { RETENTION_SECONDS } from './contracts.ts';
import type { WorkspaceOptions } from './contracts.ts';

const swap = vi.hoisted(() => ({
  before_open: undefined as ((path: string) => void) | undefined,
  before_native_open: undefined as ((name: string) => void) | undefined,
}));
vi.mock('../executor/mac/platform.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../executor/mac/platform.ts')>();
  return {
    ...actual,
    open_relative: (...args: Parameters<typeof actual.open_relative>) => {
      swap.before_native_open?.(args[1]);
      return actual.open_relative(...args);
    },
  };
});
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return {
    ...actual,
    open: async (...args: Parameters<typeof actual.open>) => {
      swap.before_open?.(String(args[0]));
      return actual.open(...args);
    },
  };
});
let root: string;
// A nested fixture belongs to its enclosing Ticket. The private root still
// bounds every filesystem operation, while product ownership checks remain intact.
const ticket =
  realpathSync(tmpdir())
    .split(sep)
    .reverse()
    .map((part) => ticket_match.exec(part)?.[1])
    .find((owner) => owner !== undefined) ?? 't_01234567';
// Every product entry and Git call is admitted against this test's fresh physical root.
function admit(path: string): string {
  path = resolve(path);
  if (!path_is_within(root, path)) throw new Error('outside synthetic root');
  let existing = path;
  while (!existsSync(existing)) existing = dirname(existing);
  if (!path_is_within(root, realpathSync(existing))) throw new Error('synthetic symlink escape');
  return path;
}
const options: WorkspaceOptions = {
  admit_path: admit,
  snapshot_ticket_scratch: () => [],
  prune_ticket_scratch: () => true,
};
beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'panels-workspaces-proof-')));
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});
function folder(name = ticket): string {
  const path = admit(join(root, 'files', 'tickets', name));
  mkdirSync(path, { recursive: true });
  return path;
}
async function command(path: string, args: string[]): Promise<string> {
  const result = await git(admit(path), args, options);
  expect(result.stderr, args.join(' ')).toBeDefined();
  expect(result.returncode, result.stderr).toBe(0);
  return result.stdout.trim();
}

test('the synthetic guard rejects outside paths and symlink escapes before product dispatch', () => {
  expect(() => admit('/home/vps/Deployments/Panels')).toThrow('outside synthetic root');
  expect(() => admit('/home/vps/Deployments/Panels/current/data/files/tickets/' + ticket)).toThrow(
    'outside synthetic root',
  );
  symlinkSync(dirname(root), join(root, 'escape'), 'dir');
  expect(() => admit(join(root, 'escape', 'missing'))).toThrow('synthetic symlink escape');
});

test('identity rejects replacement during retirement and preparation resumes the same artifact-only folder', async () => {
  const path = folder(),
    identity = workspace_identity(admit(path));
  expect(await prepare_workspace(path, null, ticket, options)).toBe(join(path, 'worktree'));
  expect(await prepare_workspace(path, null, ticket, options)).toBe(join(path, 'worktree'));
  renameSync(path, join(root, 'older'));
  mkdirSync(path);
  const replacement = workspace_identity(admit(path));
  expect(replacement.workspace_id).not.toBe(identity.workspace_id);
  expect(
    await prepare_workspace_retirement(
      path,
      { ticket_id: ticket, ended_at: 0, deleted: false },
      { ...options, identity },
    ),
  ).toEqual({ reason: 'physical workspace identity changed' });
  expect(read_workspace_identity(admit(path))).toEqual(replacement);
});

test.skipIf(!['linux', 'darwin'].includes(process.platform))(
  'inventory precedes detach and scratch deletion uses only the recorded snapshot',
  async () => {
    const path = folder();
    writeFileSync(join(path, 'artifact.txt'), 'saved');
    workspace_identity(admit(path));
    const states = new Map([[ticket, { ticket_id: ticket, ended_at: 0, deleted: false }]]);
    let before = false,
      snapshot = false,
      pruned: readonly string[] = [];
    const result = await cleanup_local_workspaces(admit(dirname(path)), states, RETENTION_SECONDS, {
      ...options,
      before_remove: () => {
        const manifest = JSON.parse(
          readFileSync(admit(join(root, 'files', 'ticket-workspaces-cleanup.json')), 'utf8'),
        );
        expect(manifest.folders[0].preservation_reason).toBeNull();
        expect(existsSync(path)).toBe(true);
        before = true;
        return null;
      },
      snapshot_ticket_scratch: () => {
        expect(before).toBe(true);
        snapshot = true;
        return [`${ticket}-123abc`];
      },
      prune_ticket_scratch: (id, names) => {
        expect(snapshot).toBe(true);
        expect(id).toBe(ticket);
        pruned = names;
        return true;
      },
    });
    expect(result).toEqual({ removed: [path], skipped: [] });
    expect(pruned).toEqual([`${ticket}-123abc`]);
    expect(existsSync(path)).toBe(false);
  },
);

test.skipIf(!['linux', 'darwin'].includes(process.platform))(
  'remote advertisements, rather than stale tracking refs, prove the last remote copy',
  async () => {
    const remote = admit(join(root, 'remote.git'));
    mkdirSync(remote);
    await command(remote, ['init', '--bare']);
    const path = folder(),
      checkout = admit(join(path, 'repository'));
    mkdirSync(checkout);
    await command(checkout, ['init']);
    await command(checkout, ['config', 'user.email', 'proof@example.test']);
    await command(checkout, ['config', 'user.name', 'Synthetic proof']);
    writeFileSync(join(checkout, 'tracked.txt'), 'published');
    await command(checkout, ['add', '.']);
    await command(checkout, ['commit', '-m', 'published']);
    await command(checkout, ['remote', 'add', 'origin', remote]);
    await command(checkout, ['push', 'origin', 'HEAD:refs/heads/published']);
    expect(await reason_to_keep(path, undefined, undefined, options)).toBeNull();
    await command(remote, ['update-ref', '-d', 'refs/heads/published']);
    expect(await reason_to_keep(path, undefined, undefined, options)).toBe(
      `no remote copy can be verified for ${checkout}`,
    );
    // The folder and its commit survive the failed proof.
    expect(readFileSync(join(checkout, 'tracked.txt'), 'utf8')).toBe('published');
    // Reuse this populated remote fixture to prove exact linked-worktree bookkeeping.
    const primary = admit(join(root, 'primary'));
    renameSync(checkout, primary);
    await command(primary, ['push', 'origin', 'HEAD:refs/heads/published']);
    const managed = await prepare_workspace(path, primary, ticket, options);
    const other = admit(join(root, 'unrelated')),
      locked = admit(join(root, 'locked'));
    await command(primary, ['worktree', 'add', '-b', 'unrelated', other]);
    await command(primary, ['worktree', 'add', '-b', 'locked', locked]);
    await command(primary, ['worktree', 'lock', locked]);
    expect(await cleanup_ticket_folder(path, options)).toBeNull();
    const listing = await command(primary, ['worktree', 'list', '--porcelain']);
    expect(listing).not.toContain(`worktree ${managed}`);
    expect(listing).not.toContain('prunable');
    expect(listing).toContain(`worktree ${other}`);
    expect(listing).toContain(`worktree ${locked}`);
    expect(listing).toContain('locked');
    expect(readFileSync(join(other, 'tracked.txt'), 'utf8')).toBe('published');
    expect(readFileSync(join(locked, 'tracked.txt'), 'utf8')).toBe('published');
    await command(primary, ['fsck', '--no-dangling']);
  },
);

test.skipIf(process.platform !== 'linux')(
  'a directory swapped for a symlink between inspection and open cannot redirect deletion',
  async () => {
    const path = folder(),
      nested = join(path, 'nested'),
      outside = join(root, 'preserved');
    mkdirSync(nested);
    mkdirSync(outside);
    writeFileSync(join(nested, 'local.txt'), 'local');
    writeFileSync(join(outside, 'sentinel.txt'), 'must survive');
    let injected = false;
    swap.before_open = (opened) => {
      if (injected || !opened.startsWith('/proc/self/fd/') || !opened.endsWith('/nested')) return;
      injected = true;
      renameSync(nested, join(root, 'detached-original'));
      symlinkSync(outside, nested, 'dir');
    };
    try {
      await expect(cleanup_ticket_folder(path, options)).rejects.toThrow(/ENOTDIR|ELOOP/);
      expect(injected).toBe(true);
      expect(readFileSync(join(outside, 'sentinel.txt'), 'utf8')).toBe('must survive');
      expect(readFileSync(join(root, 'detached-original', 'local.txt'), 'utf8')).toBe('local');
    } finally {
      swap.before_open = undefined;
    }
  },
);

test('unsupported platform removal stays in the inventory without detachment or scratch pruning', async () => {
  const path = folder();
  workspace_identity(admit(path));
  writeFileSync(join(path, 'artifact.txt'), 'preserved');
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform')!;
  const snapshot = vi.fn(() => []),
    prune = vi.fn(() => true);
  try {
    Object.defineProperty(process, 'platform', { value: 'freebsd', configurable: true });
    const result = await cleanup_local_workspaces(
      dirname(path),
      new Map([[ticket, { ticket_id: ticket, ended_at: 0, deleted: false }]]),
      RETENTION_SECONDS,
      {
        ...options,
        snapshot_ticket_scratch: snapshot,
        prune_ticket_scratch: prune,
      },
    );
    const reason = 'safe removal is not supported on this platform yet';
    expect(result).toEqual({ removed: [], skipped: [{ path, reason }] });
    const manifest = JSON.parse(
      readFileSync(join(root, 'files', 'ticket-workspaces-cleanup.json'), 'utf8'),
    );
    expect(manifest.folders[0].preservation_reason).toBe(reason);
    expect(readFileSync(join(path, 'artifact.txt'), 'utf8')).toBe('preserved');
    expect(snapshot).not.toHaveBeenCalled();
    expect(prune).not.toHaveBeenCalled();
  } finally {
    Object.defineProperty(process, 'platform', descriptor);
  }
});

test('an unsupported platform retry preserves an already detached workspace and its scratch snapshot', async () => {
  const path = folder();
  workspace_identity(admit(path));
  const target = await prepare_workspace_retirement(
    path,
    { ticket_id: ticket, ended_at: 0, deleted: false },
    options,
  );
  if ('reason' in target) throw new Error(target.reason);
  renameSync(path, target.path);
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform')!;
  const prune = vi.fn(() => true);
  try {
    Object.defineProperty(process, 'platform', { value: 'freebsd', configurable: true });
    expect(
      await retry_retired_workspaces(dirname(target.path), {
        ...options,
        prune_ticket_scratch: prune,
      }),
    ).toEqual({
      removed: [],
      skipped: [
        { path: target.path, reason: 'safe removal is not supported on this platform yet' },
      ],
    });
    expect(existsSync(target.path)).toBe(true);
    expect(prune).not.toHaveBeenCalled();
  } finally {
    Object.defineProperty(process, 'platform', descriptor);
  }
});

test.skipIf(!['linux', 'darwin'].includes(process.platform))(
  'a real directory replacement after safety proof is preserved',
  async () => {
    const path = folder();
    writeFileSync(join(path, 'original.txt'), 'original');
    const reason = await cleanup_ticket_folder(path, {
      ...options,
      before_remove: () => {
        renameSync(path, join(root, 'original-root'));
        mkdirSync(path);
        writeFileSync(join(path, 'replacement.txt'), 'replacement');
        return null;
      },
    });
    expect(reason).toBe('Workspace directory changed before removal');
    expect(readFileSync(join(path, 'replacement.txt'), 'utf8')).toBe('replacement');
    expect(readFileSync(join(root, 'original-root', 'original.txt'), 'utf8')).toBe('original');
  },
);

test.skipIf(process.platform !== 'darwin')(
  'native Mac retirement retry removes the recorded workspace and scratch snapshot',
  async () => {
    const path = folder();
    workspace_identity(admit(path));
    writeFileSync(join(path, 'artifact.txt'), 'saved');
    const target = await prepare_workspace_retirement(
      path,
      { ticket_id: ticket, ended_at: 0, deleted: false },
      { ...options, snapshot_ticket_scratch: () => [`${ticket}-saved`] },
    );
    if ('reason' in target) throw new Error(target.reason);
    renameSync(path, target.path);
    const prune = vi.fn(() => true);
    expect(
      await retry_retired_workspaces(dirname(target.path), {
        ...options,
        prune_ticket_scratch: prune,
      }),
    ).toEqual({ removed: [target.path], skipped: [] });
    expect(prune).toHaveBeenCalledWith(ticket, [`${ticket}-saved`]);
    expect(existsSync(target.path)).toBe(false);
  },
);

test.skipIf(process.platform !== 'darwin')(
  'native Mac no-follow removal preserves a replaced descendant and its symlink target',
  async () => {
    const path = folder(),
      nested = join(path, 'nested'),
      outside = join(root, 'preserved');
    mkdirSync(nested);
    mkdirSync(outside);
    writeFileSync(join(nested, 'original.txt'), 'original');
    writeFileSync(join(outside, 'sentinel.txt'), 'must survive');
    let injected = false;
    swap.before_native_open = (name) => {
      if (injected || name !== 'nested') return;
      injected = true;
      renameSync(nested, join(root, 'detached-original'));
      symlinkSync(outside, nested, 'dir');
    };
    try {
      await expect(cleanup_ticket_folder(path, options)).rejects.toThrow(/ENOTDIR|ELOOP/);
      expect(injected).toBe(true);
      expect(readFileSync(join(outside, 'sentinel.txt'), 'utf8')).toBe('must survive');
      expect(readFileSync(join(root, 'detached-original', 'original.txt'), 'utf8')).toBe(
        'original',
      );
    } finally {
      swap.before_native_open = undefined;
    }
  },
);

test.skipIf(process.platform !== 'darwin')(
  'native Mac removal refuses a replacement directory before unlinking its parent entry',
  async () => {
    const path = folder();
    writeFileSync(join(path, 'original.txt'), 'original');
    let opens = 0;
    swap.before_native_open = (name) => {
      if (name !== ticket || ++opens !== 2) return;
      renameSync(path, join(root, 'detached-original'));
      mkdirSync(path);
    };
    try {
      await expect(cleanup_ticket_folder(path, options)).rejects.toThrow(
        'Workspace directory changed before removal',
      );
      expect(opens).toBe(2);
      expect(existsSync(path)).toBe(true);
      expect(existsSync(join(root, 'detached-original'))).toBe(true);
    } finally {
      swap.before_native_open = undefined;
    }
  },
);

test.skipIf(process.platform !== 'darwin')(
  'native Mac removal yields to cancellation before it deletes held directory contents',
  async () => {
    const path = folder();
    writeFileSync(join(path, 'preserved.txt'), 'must survive');
    const cancellation = new AbortController();
    let scheduled = false;
    swap.before_native_open = (name) => {
      if (scheduled || name !== ticket) return;
      scheduled = true;
      setImmediate(() => cancellation.abort());
    };
    try {
      await expect(
        cleanup_ticket_folder(path, {
          ...options,
          signal: cancellation.signal,
        }),
      ).rejects.toMatchObject({ name: 'AbortError' });
      expect(scheduled).toBe(true);
      expect(readFileSync(join(path, 'preserved.txt'), 'utf8')).toBe('must survive');
    } finally {
      swap.before_native_open = undefined;
    }
  },
);

test.skipIf(process.platform !== 'darwin')(
  'native Mac metadata classifies sockets and FIFOs without opening them before removal',
  async () => {
    const path = folder(),
      socket = join(path, 's'),
      fifo = join(path, 'pipe');
    const server = createServer(),
      commands = new BackendCommands();
    try {
      await new Promise<void>((resolve, reject) => {
        server.once('error', reject);
        server.listen(socket, () => resolve());
      });
      const created = await commands.run(['/usr/bin/mkfifo', fifo], { timeout_seconds: 5 });
      expect(created.exit_code, created.standard_error).toBe(0);
      writeFileSync(join(path, 'regular'), 'saved');
      mkdirSync(join(path, 'directory'));
      symlinkSync('regular', join(path, 'link'));
      const descriptor = open_directory_descriptor(path);
      try {
        for (const name of ['s', 'pipe', 'regular', 'directory', 'link']) {
          const actual = lstatSync(join(path, name));
          expect(stat_darwin_entry(descriptor, name)).toEqual({
            dev: actual.dev,
            ino: actual.ino,
            mode: actual.mode,
          });
        }
      } finally {
        close_native_descriptor(descriptor);
      }
      expect(lstatSync(socket).isSocket()).toBe(true);
      expect(lstatSync(fifo).isFIFO()).toBe(true);
      expect(await cleanup_ticket_folder(path, options)).toBeNull();
      expect(existsSync(path)).toBe(false);
    } finally {
      try {
        await commands.shutdown();
      } finally {
        if (server.listening)
          await new Promise<void>((resolve, reject) => {
            server.close((error) => (error ? reject(error) : resolve()));
          });
      }
    }
  },
);
