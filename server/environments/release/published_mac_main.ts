/** Before publication, the home's real Mac preparation must accept the exact signed ZIP. */
import { copyFile, mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { FileUpdateAttemptStore } from '../updates/attempts.ts';
import { parse_release } from '../updates/codec.ts';
import { MacUpdateService } from '../updates/mac/service.ts';
import { PUBLICATION_RECEIPT_NAME } from './configuration.ts';

// The publisher uploads inputs/mac.zip only when its digest equals the checked Actions artifact.
export async function check_published_mac(publication_root: string): Promise<string> {
  const receipt = JSON.parse(
    await readFile(join(publication_root, 'receipt', PUBLICATION_RECEIPT_NAME), 'utf8'),
  );
  // The home's provider adds the identity of the publication asset in the same way.
  const release = parse_release({ ...receipt, proof_artifact_id: 1 });
  const zip = join(publication_root, 'inputs', 'mac.zip');
  if (release.mac === null) throw new Error('The publication receipt has no Mac package');
  const root = await mkdtemp(join(tmpdir(), 'panels-published-mac-'));
  const children = new ChildProcesses();
  const controller = new AbortController();
  try {
    const service = new MacUpdateService({
      root,
      machines: () => [
        {
          machine_id: ORIGINAL_MAC_MACHINE_ID,
          name: 'Published Mac package check',
          platform: 'darwin',
          present: false,
          identity: null,
        },
      ],
      platform_for: () => 'darwin',
      attempts: new FileUpdateAttemptStore(join(root, 'attempt.json'), () => {}),
      provider: {
        available: async () => release,
        download_artifact: async (artifact_id, destination) => {
          if (artifact_id !== release.mac!.artifact_id)
            throw new Error('The home asked for another Mac artifact');
          await copyFile(zip, destination);
        },
      },
      current_app_sha: release.manifest.app_sha,
      child_processes: children,
      signal: controller.signal,
      ready_release: () => release,
      emit_change: () => {},
    });
    await service.prepare(release);
    await service.settled();
    // Preparation records a refusal for the machine and does not throw it.
    const detail = service.machine_states(release)[0]?.detail ?? null;
    if (detail !== null) throw new Error('The home refuses the published Mac package: ' + detail);
    const archive = join(root, 'archives', release.mac.archive_sha256 + '.tar.gz');
    if ((await stat(archive)).size !== release.mac.archive_size)
      throw new Error('The home prepared a different Mac package');
    return release.mac.archive_sha256;
  } finally {
    controller.abort();
    children.seal();
    await children.settled();
    await rm(root, { recursive: true, force: true });
  }
}

if (process.argv.length !== 3) throw new Error('Expected the publication directory');
const digest = await check_published_mac(resolve(process.argv[2]!));
console.info(`The home accepts the published Mac package ${digest}.`);
