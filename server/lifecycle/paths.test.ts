/** Native path safety needs real filesystem metadata, with no sockets or services. */
import { chmodSync, lstatSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { application_process_detached, require_private_runtime_directory } from './paths.ts';
import { darwin_control_socket_path } from './logic/paths.ts';

test('Darwin private runtime refuses foreign ownership, symlinks and public permissions without repairs', () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-paths-'));
  const path = join(root, 'runtime');
  // The public-directory refusal needs an explicit mask, independent of the runner.
  const inherited_mask = process.umask(0o022);
  try {
    expect(require_private_runtime_directory(path)).toBe(path);
    expect(lstatSync(path).mode & 0o777).toBe(0o700);
    const uid = process.getuid!();
    vi.spyOn(process, 'getuid').mockReturnValueOnce(uid + 1);
    expect(() => require_private_runtime_directory(path)).toThrow('private and owned');
    chmodSync(path, 0o755);
    expect(() => require_private_runtime_directory(path)).toThrow('private and owned');
    expect(lstatSync(path).mode & 0o777).toBe(0o755);
    rmSync(path, { recursive: true });
    const foreign = join(root, 'foreign');
    mkdirSync(foreign);
    chmodSync(foreign, 0o755);
    symlinkSync(foreign, path);
    expect(() => require_private_runtime_directory(path)).toThrow('private and owned');
    expect(lstatSync(foreign).mode & 0o777).toBe(0o755);
  } finally {
    process.umask(inherited_mask);
    vi.restoreAllMocks();
    rmSync(root, { recursive: true, force: true });
  }
});

test('native lifecycle refuses unsupported platforms before process custody effects', () => {
  expect(application_process_detached('linux')).toBe(true);
  expect(application_process_detached('darwin')).toBe(false);
  expect(() => application_process_detached('win32')).toThrow('Linux or macOS');
});

test('Darwin address is byte bounded, stable and shared by original config and APP override', () => {
  const long = '/source/' + 'ü'.repeat(64) + '/control.sock';
  const runtime = '/var/tmp/panels-123';
  const control = darwin_control_socket_path(long, runtime);
  expect(control).toBe('/var/tmp/panels-123/s-97f4633e1e724f8b092d667a.sock');
  expect(Buffer.byteLength(control)).toBeLessThan(104);
  expect(darwin_control_socket_path(control, runtime)).toBe(control);
  expect(darwin_control_socket_path(long + 'x', runtime)).not.toBe(control);
  expect(darwin_control_socket_path('/' + 'x'.repeat(102), runtime)).toHaveLength(103);
});
