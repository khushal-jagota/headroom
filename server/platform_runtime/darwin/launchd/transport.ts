/** Separate authenticated byte streams retain ordinary child-pipe semantics. */
import { createConnection, type Socket } from 'node:net';
import { lstat, readdir, rmdir, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { NICE, TERM_SECONDS } from '../../../worker_supervision/config.ts';

export const channels = ['stdin', 'stdout', 'stderr', 'control'] as const;
export type Channel = (typeof channels)[number];
export function connect_channel(
  directory: string,
  token: string,
  channel: Channel,
): Promise<Socket> {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ path: join(directory, channel), allowHalfOpen: true });
    // The backend inherits stdin's descriptor. Never pre-read its bytes here.
    socket.pause();
    socket.once('error', reject);
    socket.once('connect', () => {
      socket.write(`${token}\n`, () => resolve(socket));
    });
  });
}
export async function remove_transport(directory: string): Promise<void> {
  if (!/^\/(?:private\/)?tmp\/panels-worker-[A-Za-z0-9]+$/.test(directory))
    throw new Error('Invalid Mac Worker transport directory');
  let info;
  try {
    info = await lstat(directory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
    throw error;
  }
  if (
    !info.isDirectory() ||
    info.isSymbolicLink() ||
    info.uid !== process.getuid!() ||
    (info.mode & 0o077) !== 0
  )
    throw new Error('Mac Worker transport directory is not private and owned');
  const allowed = new Set<string>([...channels, 'job.plist']);
  for (const name of await readdir(directory).catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'ENOENT') return [];
    throw error;
  })) {
    if (!allowed.has(name))
      throw new Error('Mac Worker transport directory contains an unknown file');
    await unlink(join(directory, name)).catch((error: NodeJS.ErrnoException) => {
      if (error.code !== 'ENOENT') throw error;
    });
  }
  await rmdir(directory).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error;
  });
}
const escape_xml = (value: string): string =>
  value.replace(
    /[<>&"']/g,
    (character) =>
      ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[character]!,
  );
export function job_plist(
  label: string,
  argv: readonly string[],
  environment: Readonly<Record<string, string>>,
  cwd: string,
): string {
  const string = (value: string) => `<string>${escape_xml(value)}</string>`;
  return `<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>Label</key>${string(label)}<key>ProgramArguments</key><array>${argv.map(string).join('')}</array><key>WorkingDirectory</key>${string(cwd)}<key>EnvironmentVariables</key><dict>${Object.entries(
    environment,
  )
    .map(([key, value]) => `<key>${escape_xml(key)}</key>${string(value)}`)
    .join(
      '',
    )}</dict><key>RunAtLoad</key><true/><key>KeepAlive</key><false/><key>AbandonProcessGroup</key><false/><key>Nice</key><integer>${NICE}</integer><key>ExitTimeOut</key><integer>${TERM_SECONDS}</integer></dict></plist>\n`;
}
