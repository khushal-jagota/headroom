/** Readonly candidate schema decisions, without an installer or migration writer. */
import { mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import { createReadAccess } from '../../core/database/index.ts';
import { validate_app_manifest } from '../release/identity.ts';
import { AppValidationError } from '../release/contracts.ts';
import type { AppManifest } from '../release/contracts.ts';
import { read_migration_graph, require_node_database_frontier } from '../release/migrations.ts';
import { run_runtime_json_probe } from '../release/admission_process.ts';
import { RUNTIME_PROBE_TIMEOUT_SECONDS } from '../release/process_configuration.ts';
import type { InstallerOperations } from './contracts.ts';
import { PYTHON_SCHEMA_STATUS_PROBE } from './migration_probe.ts';
import { resolved_path } from '../paths.ts';

async function python_schema_change_pending(
  database: string,
  root: string,
  manifest: AppManifest,
  deadline: number,
  child_processes: OwnedChildProcesses,
): Promise<boolean> {
  if (manifest.runtime_contract?.version !== 1)
    throw new AppValidationError('Python schema status requires an admitted version-1 runtime');
  const state = await mkdtemp(join(tmpdir(), 'panels-schema-probe-'));
  try {
    for (const name of [
      'config',
      'cache',
      'codex',
      'claude',
      'hermes',
      'data',
      'logs',
      'backups',
      'app',
      'workspace',
      'scratch',
    ])
      await mkdir(join(state, name), { mode: 0o700 });
    await writeFile(join(state, 'config.yaml'), '{}\n', { mode: 0o600 });
    const result = await run_runtime_json_probe(
      [
        join(root, '.venv/bin/python'),
        '-I',
        '-B',
        '-c',
        PYTHON_SCHEMA_STATUS_PROBE,
        root,
        manifest.runtime_contract.python_version,
        database,
      ],
      state,
      deadline,
      child_processes,
    );
    if (
      result !== null &&
      typeof result === 'object' &&
      !Array.isArray(result) &&
      Object.keys(result).length === 1
    ) {
      if ('schema_change_pending' in result && typeof result.schema_change_pending === 'boolean')
        return result.schema_change_pending;
      if ('error' in result && typeof result.error === 'string')
        throw new AppValidationError(result.error);
    }
    throw new AppValidationError('Python schema probe returned an incompatible response');
  } finally {
    // The probe owns its actual close; it never joins unrelated invocation children.
    await rm(state, { recursive: true, force: true });
  }
}

export function create_migration_status(
  child_processes: OwnedChildProcesses,
): Pick<InstallerOperations, 'require_exact_schema_frontier' | 'schema_change_pending'> {
  return {
    async require_exact_schema_frontier(database, staged_app) {
      const graph = await read_migration_graph(staged_app);
      const reader = createReadAccess(database, { busyTimeoutMs: 0 });
      try {
        reader.read((sql) => require_node_database_frontier(sql, graph));
      } finally {
        reader.close();
      }
    },
    async schema_change_pending(database, staged_app) {
      const deadline = performance.now() / 1000 + RUNTIME_PROBE_TIMEOUT_SECONDS;
      const canonical_database = await resolved_path(
        isAbsolute(database) ? database : process.cwd() + '/' + database,
      );
      const root = await realpath(staged_app);
      const manifest = await validate_app_manifest(join(root, 'manifest.json'), {
        require_runtime: true,
        verify_artifact_digest: false,
      });
      return python_schema_change_pending(
        canonical_database,
        root,
        manifest,
        deadline,
        child_processes,
      );
    },
  };
}
