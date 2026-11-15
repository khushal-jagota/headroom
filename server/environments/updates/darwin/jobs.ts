/** Native plist conversion and exclusive writes share the retained directory bridge. */
import { constants, fstatSync, fsyncSync, ftruncateSync, lstatSync, readFileSync, writeSync } from 'node:fs';
import { basename, dirname, isAbsolute, join } from 'node:path';
import { is_record } from '../../../core/records.ts';
import { close_native_descriptor, open_directory_descriptor, open_relative, readlink_relative, symlink_relative } from '../../../executor/mac/platform.ts';
import { read_owned_home_file } from './identity.ts';
import { resolved_path } from '../../paths.ts';
import { assert_release_execution } from '../../release/process.ts';
import { DARWIN_HOME_OBSERVATION_SECONDS } from './configuration.ts';
import type { DarwinHomeIO, DarwinHomeJob } from './contracts.ts';
import type { ReleaseExecution } from '../../release/contracts.ts';

export async function read_home_job(path: string, io: DarwinHomeIO, execution = io.execution): Promise<DarwinHomeJob> {
  const before = read_owned_home_file(path, io.uid);
  const result = await io.command(['/usr/bin/plutil', '-convert', 'json', '-o', '-', path], execution, DARWIN_HOME_OBSERVATION_SECONDS);
  if (!read_owned_home_file(path, io.uid).equals(before)) throw new Error(`Mac home plist changed: ${path}`);
  const value: unknown = JSON.parse(result.stdout);
  if (!is_record(value) || typeof value.Label !== 'string' || typeof value.WorkingDirectory !== 'string' ||
    !Array.isArray(value.ProgramArguments) || !value.ProgramArguments.every((item) => typeof item === 'string') ||
    !is_record(value.EnvironmentVariables) || !Object.values(value.EnvironmentVariables).every((item) => typeof item === 'string'))
    throw new Error(`Mac home plist is invalid: ${path}`);
  return value as unknown as DarwinHomeJob;
}
export async function write_home_job(path: string, value: DarwinHomeJob, io: DarwinHomeIO, previous?: Buffer): Promise<void> {
  assert_release_execution(io.execution);
  const parent = open_directory_descriptor(dirname(path));
  let fd: number | undefined;
  const contents = Buffer.from(JSON.stringify(value) + '\n');
  try {
    fd = open_relative(parent, basename(path), constants.O_RDWR | constants.O_NOFOLLOW | constants.O_NONBLOCK |
      (previous === undefined ? constants.O_CREAT | constants.O_EXCL : 0), 0o600);
    const proof = fstatSync(fd), root = fstatSync(parent);
    if (!proof.isFile() || proof.uid !== io.uid || proof.nlink !== 1 || (previous && !readFileSync(fd).equals(previous)))
      throw new Error(`Mac job write no longer owns its file: ${path}`);
    const current = (expected: Buffer) => {
      const directory = lstatSync(dirname(path));
      const check = open_relative(parent, basename(path), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      try {
        const actual = fstatSync(check);
        if (!directory.isDirectory() || directory.dev !== root.dev || directory.ino !== root.ino ||
          actual.dev !== proof.dev || actual.ino !== proof.ino || !readFileSync(check).equals(expected))
          throw new Error(`Mac job file changed during conversion: ${path}`);
      } finally { close_native_descriptor(check); }
    };
    const write = (descriptor: number, bytes: Buffer) => {
      assert_release_execution(io.execution);
      const info = fstatSync(descriptor);
      if (!info.isFile() || info.uid !== io.uid || info.nlink !== 1)
        throw new Error(`Mac job file lost exclusive ownership before write: ${path}`);
      ftruncateSync(descriptor, 0);
      let offset = 0;
      while (offset < bytes.length) {
        const count = writeSync(descriptor, bytes, offset, bytes.length - offset, offset);
        if (count <= 0) throw new Error('Mac job file write did not advance');
        offset += count;
      }
      fsyncSync(descriptor);
    };
    write(fd, contents);
    const result = await io.command(['/usr/bin/plutil', '-convert', 'xml1', '-o', '-', path], io.execution, DARWIN_HOME_OBSERVATION_SECONDS);
    current(contents);
    // Reset the shared descriptor position before replacement through its retained inode.
    const converted = Buffer.from(result.stdout);
    if (!converted.length) throw new Error('Native plist conversion returned no content');
    const replacement = open_relative(parent, basename(path), constants.O_WRONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const actual = fstatSync(replacement);
      if (actual.dev !== proof.dev || actual.ino !== proof.ino || actual.nlink !== 1) throw new Error(`Mac plist replacement changed: ${path}`);
      write(replacement, converted);
    } finally { close_native_descriptor(replacement); }
  } finally {
    try {
      if (fd !== undefined) close_native_descriptor(fd);
    } finally { close_native_descriptor(parent); }
    // A failed conversion retains its file. A caller cannot mistake uncertainty for permission to overwrite it.
  }
}
export function darwin_schedule_jobs(root: string, label: string, command: readonly string[], environment: Readonly<Record<string, string>>, working_directory = root): Record<string, DarwinHomeJob> {
  const shared = { WorkingDirectory: working_directory, EnvironmentVariables: environment,
    LimitLoadToSessionType: 'Aqua', AbandonProcessGroup: false };
  return {
    [label + '.maintenance']: { ...shared, Label: label + '.maintenance',
      ProgramArguments: [...command, 'environment', 'cleanup', '--apply'], StartCalendarInterval: { Hour: 3, Minute: 15 },
      StandardOutPath: join(root, 'logs/maintenance.stdout.log'), StandardErrorPath: join(root, 'logs/maintenance.stderr.log') },
    [label + '.stall-watchdog']: { ...shared, Label: label + '.stall-watchdog',
      ProgramArguments: [...command, 'stalls', 'watchdog', '--db', join(root, 'data/planner.db'), '--state-dir', join(root, 'data/stall-watchdog')],
      StartInterval: 30, StandardOutPath: join(root, 'logs/stall-watchdog.stdout.log'), StandardErrorPath: join(root, 'logs/stall-watchdog.stderr.log') },
  };
}
export async function persistent_home_link(home: string, label: string, expected: string): Promise<boolean> {
  const path = join(home, 'Library/LaunchAgents');
  let parent: number;
  try { parent = open_directory_descriptor(path); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false; throw error; }
  try {
    const actual = readlink_relative(parent, label + '.plist');
    return typeof actual === 'string' && await resolved_path(isAbsolute(actual) ? actual : join(path, actual)) === await resolved_path(expected);
  }
  finally { close_native_descriptor(parent); }
}
export async function check_home_login_link(home: string, label: string, expected: string): Promise<void> {
  const parent = open_directory_descriptor(join(home, 'Library/LaunchAgents'));
  try {
    const actual = readlink_relative(parent, label + '.plist');
    if (actual !== undefined && (typeof actual !== 'string' ||
      await resolved_path(isAbsolute(actual) ? actual : join(home, 'Library/LaunchAgents', actual)) !== await resolved_path(expected)))
      throw new Error('Mac auxiliary login link belongs to another installation');
  } finally { close_native_descriptor(parent); }
}
export async function ensure_home_login_link(home: string, label: string, expected: string, execution: ReleaseExecution): Promise<void> {
  await check_home_login_link(home, label, expected);
  const parent = open_directory_descriptor(join(home, 'Library/LaunchAgents'));
  try {
    const actual = readlink_relative(parent, label + '.plist');
    if (actual === undefined) {
      assert_release_execution(execution);
      symlink_relative(parent, label + '.plist', expected);
    } else if (typeof actual !== 'string' || await resolved_path(isAbsolute(actual) ? actual : join(home, 'Library/LaunchAgents', actual)) !== await resolved_path(expected))
      throw new Error('Mac auxiliary login link belongs to another installation');
  } finally { close_native_descriptor(parent); }
}
