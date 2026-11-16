/** Shared synthetic package for archive and update-controller checks. */
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { Header } from 'tar';
import type { RuntimeTarget } from '../../../../environments/release/contracts.ts';
import {
  GLIBC_LIBRARIES,
  SUPPORTED_CPU_FEATURES,
  SUPPORTED_PLATFORMS,
} from '../../../../environments/updates/logic/runtime_contract.ts';
export function runtime_archive(
  revision: string,
  unsafe = false,
  target?: RuntimeTarget,
  entrypoint = 'synthetic entrypoint',
) {
  const manifest = {
    schema_version: 1,
    application_revision: revision,
    platform: target?.system ?? 'darwin',
    architecture: target?.architecture ?? 'arm64',
    ...(target ? { role: target.role } : {}),
  };
  const inventory = '{}';
  const contract = {
    version: 2,
    system: 'linux',
    architecture: 'x86_64',
    node_major: 22,
    minimum_node_version: '22.18.0',
    platforms: SUPPORTED_PLATFORMS,
    cpu_features: SUPPORTED_CPU_FEATURES,
    external_libraries: GLIBC_LIBRARIES,
    external_executables: ['sh', 'node'],
    inventory_sha256: createHash('sha256').update(inventory).digest('hex'),
  };
  const executable =
    target?.system === 'linux'
      ? [
          { path: 'bin/panels', data: '#!/bin/sh\n', mode: 0o755 },
          {
            path: 'manifest.json',
            data: JSON.stringify({
              format: 'panels-app-v1',
              app_sha: revision,
              source_digest: 'b'.repeat(64),
              artifact_digest: 'c'.repeat(64),
              runtime_contract: contract,
            }),
          },
          { path: 'runtime/contract.json', data: JSON.stringify(contract) },
          { path: 'runtime/inventory.json', data: inventory },
        ]
      : [
          { path: 'node/bin/node', data: 'synthetic executable', mode: 0o755 },
          { path: 'bin/panels', data: '', link: '../server/executor/mac/entrypoint.ts' },
        ];
  const members: { path: string; data: string; mode?: number; link?: string }[] = unsafe
    ? [{ path: '../escape', data: 'outside' }]
    : [
        { path: 'mac-runtime.json', data: JSON.stringify(manifest) },
        ...executable,
        { path: 'server/executor/mac/entrypoint.ts', data: entrypoint },
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
