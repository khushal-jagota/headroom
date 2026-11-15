/** Reuse the archive child and recorded admission, without a service or candidate execution. */
import { createWriteStream } from 'node:fs';
import { copyFile, mkdir, readFile, readdir } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { isDeepStrictEqual } from 'node:util';
import type { SpawnOptions } from 'node:child_process';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { MAC_HOME } from '../../release/contracts.ts';
import { parse_release } from '../codec.ts';
import { create_release_staging } from '../staging.ts';
import { with_zip_member } from '../zip.ts';
import {
  mac_home_receipt_options,
  validate_mac_home_receipt,
} from '../../release/mac_home_receipt.ts';
import { remove_owned_tree } from '../../release/archive.ts';
import type { PublicationSourceRun } from '../../release/publication_contracts.ts';

export async function probe_home_receipt(spec: {
  root: string;
  source_run: PublicationSourceRun;
  cases: ReadonlyArray<{ name: string; options: Record<string, unknown> }>;
}): Promise<unknown> {
  const owner = new ChildProcesses(),
    controller = new AbortController(),
    answers: unknown[] = [];
  const spawn = owner.spawn.bind(owner);
  let spawned = 0, closed = 0;
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    if (command !== process.execPath || !args[0]?.endsWith('/release/archive_unpack_main.ts'))
      throw new Error('Unexpected native launch');
    const child = spawn(command, args, options);
    spawned++;
    child.once('close', () => { closed++; });
    return child;
  }) as typeof owner.spawn;
  try {
    for (const item of spec.cases) {
      const work = join(spec.root, item.name);
      await mkdir(work);
      try {
        const options = mac_home_receipt_options(item.options);
        const result = options
          ? await validate_mac_home_receipt(
              options,
              spec.source_run,
              work,
              {
                child_processes: owner,
                signal: controller.signal,
                deadline: performance.now() / 1000 + 10,
              },
              (zip, name, destination) =>
                with_zip_member(zip, name, (input) =>
                  pipeline(input, createWriteStream(destination, { flags: 'wx' }), {
                    signal: controller.signal,
                  }),
                ),
            )
          : null;
        answers.push({ name: item.name, result, error: null });
      } catch (error) {
        if (!(error instanceof Error)) throw error;
        answers.push({ name: item.name, result: null, error: error.message });
      } finally {
        await remove_owned_tree(work);
      }
    }
    return { answers, spawned, closed };
  } finally {
    owner.seal();
    controller.abort();
    await owner.settled();
  }
}

export async function probe_home_target_staging(spec: {
  root: string;
  mac: { artifact: string; release: Record<string, unknown> };
  linux: { artifact: string; release: Record<string, unknown> };
}): Promise<unknown> {
  const owner = new ChildProcesses(),
    controller = new AbortController(),
    spawn = owner.spawn.bind(owner);
  let spawned = 0,
    closed = 0;
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    if (command !== process.execPath || !args[0]?.endsWith('/release/archive_unpack_main.ts'))
      throw new Error('Unexpected native launch');
    const child = spawn(command, args, options);
    spawned++;
    child.once('close', () => {
      closed++;
    });
    return child;
  }) as typeof owner.spawn;
  const rows: unknown[] = [];
  try {
    for (const [system, item] of Object.entries({ mac: spec.mac, linux: spec.linux })) {
      const receipt = parse_release(item.release);
      let downloads = 0,
        admissions = 0;
      const admit = async (_path: string, manifest: unknown) => {
        if (!isDeepStrictEqual(manifest, receipt.manifest))
          throw new Error('Admission lost the selected receipt');
        admissions++;
      };
      const stage =
        system === 'mac'
          ? create_release_staging(owner, controller.signal, admit, MAC_HOME)
          : create_release_staging(owner, controller.signal, admit);
      const provider = {
        available: async () => null,
        download_artifact: async (id: number, destination: string) => {
          if (id !== receipt.artifact_id) throw new Error('Stage selected a different artifact');
          downloads++;
          await copyFile(item.artifact, destination);
        },
      };
      for (const kind of system === 'mac' ? ['fresh', 'reuse', 'checksum'] : ['fresh']) {
        const current = join(spec.root, system, kind === 'checksum' ? 'checksum' : 'current');
        const changed =
          kind === 'checksum' ? { ...receipt, archive_sha256: '0'.repeat(64) } : receipt;
        let returned: string | null = null,
          error: string | null = null;
        try {
          returned = basename(await stage.stage(provider, changed, current));
        } catch (cause) {
          if (!(cause instanceof Error)) throw cause;
          error = cause.message;
        }
        rows.push({
          system,
          kind,
          returned,
          error,
          files: (await readdir(current)).sort(),
          downloads,
          admissions,
          sentinel: await readFile(join(spec.root, 'outside/sentinel'), 'utf8'),
          marker: returned ? await readFile(join(current, returned, 'new'), 'utf8') : null,
        });
      }
    }
    return { rows, spawned, closed };
  } finally {
    owner.seal();
    controller.abort();
    await owner.settled();
  }
}
