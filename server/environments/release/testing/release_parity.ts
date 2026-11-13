/** Export-only identity/archive proof. Inputs are the driver's private files. */
import { lstat, readlink, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { createReadAccess } from '../../../core/database/index.ts';
import type { ElfObject, MigrationGraph } from '../contracts.ts';
import { APP_FORMAT } from '../configuration.ts';
import { create_app_archive, extract_app_archive } from '../archive.ts';
import { digest_app_artifact, digest_app_source, validate_app_manifest } from '../identity.ts';
import { require_node_database_frontier, schema_change_pending } from '../migrations.ts';
import { parse_dynamic } from '../native/elf.ts';
import { resolve_provider } from '../native/providers.ts';
import { RuntimeContractError } from '../../updates/logic/runtime_contract.ts';
import { complete_node_runtime } from '../native_inventory.ts';
import { AppValidationError } from '../contracts.ts';

export async function native_loader_parity(spec: {
  dynamic: readonly [string, string];
  providers: readonly { name: string; requester: ElfObject; ancestors: readonly ElfObject[] }[];
}): Promise<unknown> {
  const providers = [];
  for (const request of spec.providers) {
    try {
      providers.push({
        provider: await resolve_provider(
          request.name,
          request.requester,
          request.ancestors,
          new Map(),
        ),
      });
    } catch (error) {
      if (!(error instanceof RuntimeContractError)) throw error;
      providers.push({ error: error.message });
    }
  }
  return { dynamic: parse_dynamic(...spec.dynamic), providers };
}

async function refused(work: () => unknown): Promise<boolean> {
  try {
    await work();
    return false;
  } catch {
    return true;
  }
}
export async function release_parity(root: string, refusals: readonly string[]): Promise<unknown> {
  const app = join(root, 'app');
  const manifest = await validate_app_manifest(join(app, 'manifest.json'), {
    require_runtime: true,
  });
  const extracted = join(root, 'node-from-python');
  const { hash_archive } = await import('../archive.ts');
  const archive = join(root, 'python.tar.gz');
  const restored = await extract_app_archive(
    archive,
    extracted,
    await hash_archive(archive),
    manifest.app_sha,
  );
  const sealed = await create_app_archive(app, join(root, 'node.tar.gz'));
  const graph: MigrationGraph = {
    version: 1,
    source_digest: 'a'.repeat(64),
    revisions: { initial: [], data_only: ['initial'] },
  };
  const frontier = [];
  for (const name of ['current', 'ancestor', 'missing', 'unknown']) {
    const access = createReadAccess(join(root, name + '.sqlite'));
    try {
      let pending: boolean | null = null;
      let pending_error: string | null = null,
        node_error: string | null = null;
      try {
        pending = access.read((sql) => schema_change_pending(sql, graph));
      } catch (error) {
        if (!(error instanceof AppValidationError)) throw error;
        pending_error = error.message;
      }
      try {
        access.read((sql) => require_node_database_frontier(sql, graph));
      } catch (error) {
        if (!(error instanceof AppValidationError)) throw error;
        node_error = error.message;
      }
      frontier.push({ pending, pending_error, node_refused: node_error !== null, node_error });
    } finally {
      access.close();
    }
  }
  const native_tree_admission = [];
  for (const target of ['provider', 'inventory', 'contract']) {
    try {
      await complete_node_runtime(join(root, 'native-link-' + target));
      throw new Error('native packaging admitted an outside write target');
    } catch (error) {
      if (
        !(error instanceof AppValidationError) ||
        error.message !== 'app contains a symlink outside its root'
      )
        throw error;
      native_tree_admission.push({ target, error: error.message });
    }
  }
  return {
    identity: {
      source: await digest_app_source(app),
      artifact: await digest_app_artifact(app),
      manifest: { format: APP_FORMAT, ...manifest },
    },
    legacy: {
      format: APP_FORMAT,
      ...(await validate_app_manifest(join(root, 'legacy/manifest.json'))),
    },
    restored: { format: APP_FORMAT, ...restored },
    restored_mode: (await stat(join(extracted, 'sentinel'))).mode & 0o7777,
    restored_link: await readlink(join(extracted, 'bin/alias')),
    node_archive_sha256: sealed,
    exclusive_refused: await refused(() => create_app_archive(app, join(root, 'node.tar.gz'))),
    hash_refused: await refused(() =>
      extract_app_archive(archive, join(root, 'bad-hash'), '0'.repeat(64), manifest.app_sha),
    ),
    refusals: await Promise.all(
      refusals.map(async (name) => {
        const input = join(root, name + '.tar.gz');
        const destination = join(root, 'node-' + name);
        const denied = await refused(async () => {
          await extract_app_archive(
            input,
            destination,
            await hash_archive(input),
            manifest.app_sha,
          );
        });
        let absent = false;
        try {
          await lstat(destination);
        } catch (error) {
          if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
          absent = true;
        }
        return { archive: name, refused: denied, destination_absent: absent };
      }),
    ),
    node_protocol: {
      accepted: !(await refused(() =>
        validate_app_manifest(join(root, 'v2/manifest.json'), { require_runtime: true }),
      )),
      missing_refused: await refused(() =>
        validate_app_manifest(join(root, 'v2-missing/manifest.json'), { require_runtime: true }),
      ),
      python_field_refused: await refused(() =>
        validate_app_manifest(join(root, 'v2-extra/manifest.json'), { require_runtime: true }),
      ),
    },
    frontier,
    native_tree_admission,
  };
}
