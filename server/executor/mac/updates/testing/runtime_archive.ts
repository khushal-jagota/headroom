/** Shared synthetic package for archive and update-controller checks. */
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { Header } from 'tar';
export function runtime_archive(revision: string, unsafe = false) {
  const manifest = {
    schema_version: 1,
    application_revision: revision,
    platform: 'darwin',
    architecture: 'arm64',
  };
  const members = unsafe
    ? [{ path: '../escape', data: 'outside' }]
    : [
        { path: 'mac-runtime.json', data: JSON.stringify(manifest) },
        { path: 'node/bin/node', data: 'synthetic executable', mode: 0o755 },
        { path: 'server/executor/mac/entrypoint.ts', data: 'synthetic entrypoint' },
        { path: 'bin/panels', data: '', link: '../server/executor/mac/entrypoint.ts' },
      ];
  const blocks: Buffer[] = [];
  for (const member of members) {
    const data = Buffer.from(member.data),
      link = 'link' in member ? member.link : undefined;
    const header = new Header({
      path: member.path,
      type: link ? 'SymbolicLink' : 'File',
      size: data.length,
      mode: 'mode' in member ? member.mode : 0o644,
      mtime: new Date(0),
      linkpath: link,
    });
    const encoded = Buffer.alloc(512);
    header.encode(encoded);
    blocks.push(encoded, data, Buffer.alloc((512 - (data.length % 512)) % 512));
  }
  const bytes = gzipSync(Buffer.concat([...blocks, Buffer.alloc(1024)]));
  return { bytes, sha: createHash('sha256').update(bytes).digest('hex') };
}
