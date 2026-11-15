/** Private snapshot proof. Synthetic observations grant no native process authority. */
import { mkdir, readFile, writeFile, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import type { WorkerProcess } from '../../../worker_supervision/contracts.ts';
import type { CleanupCommandEvidence, CleanupSnapshotOptions } from '../cleanup_contracts.ts';
import { snapshot_cleanup } from '../cleanup_reconciliation.ts';

const began = performance.now(),
  root = process.argv[2]!;
const input = JSON.parse(await readFile(join(root, 'input.json'), 'utf8')) as {
  commands: CleanupCommandEvidence[];
  records: { name: string; record: unknown; mode: number }[];
  processes: WorkerProcess[];
  steps: {
    label: string;
    options?: CleanupSnapshotOptions;
    receipt?: unknown;
    legacy?: number[];
    ended?: number[];
    readable?: string[];
  }[];
};
const run = join(root, 'run'),
  journals = join(run, 'worker-processes');
await mkdir(journals, { recursive: true, mode: 0o700 });
for (const { name, record, mode } of input.records) {
  await writeFile(join(journals, name), JSON.stringify(record), { mode });
}
const table = new Map(input.processes.map((item) => [item.pid, item]));
let legacy = new Set<number>();
const runtime = new WorkerSupervisionRuntime({
  primitives: {
    owns_isolated_session_members: false,
    requires_containment: false,
    host_boot: async () => ({ host: 'host', boot: 'boot' }),
    snapshot: async () => new Map(table),
    is_absent: async (item) => table.get(item.pid)?.birth !== item.birth,
    signal: async () => {
      throw new Error('A snapshot cannot signal');
    },
    prepare_launch: () => {
      throw new Error('A snapshot cannot launch');
    },
  },
});
try {
  const points: unknown[] = [];
  for (const step of input.steps) {
    if (step.receipt !== undefined)
      await writeFile(join(run, 'legacy-worker-cleanup.json'), JSON.stringify(step.receipt), {
        mode: 0o600,
      });
    if (step.legacy !== undefined) legacy = new Set(step.legacy);
    for (const pid of step.ended ?? []) table.delete(pid);
    for (const name of step.readable ?? []) await chmod(join(journals, name), 0o600);
    points.push({
      label: step.label,
      ...(await snapshot_cleanup(
        { run },
        {
          runtime,
          async *read_commands() {
            for (const [index, row] of input.commands.entries())
              yield index % 2
                ? row
                : {
                    command_json: Buffer.from(row.command_json as string),
                    result_json:
                      row.result_json === null ? null : Buffer.from(row.result_json as string),
                  };
          },
          files: {
            async read_runtime_record(path) {
              try {
                return await readFile(path, 'utf8');
              } catch (error) {
                if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
                throw error;
              }
            },
            async atomic_runtime_record() {
              throw new Error('A snapshot cannot write a legacy receipt');
            },
            async remove_runtime_record() {
              throw new Error('A snapshot cannot delete a legacy receipt');
            },
          },
          inspect_legacy: async (identity) => legacy.has(identity.process_id),
        },
        step.options,
      )),
    });
  }
  console.log(
    JSON.stringify({ parity: { points }, elapsed_seconds: (performance.now() - began) / 1000 }),
  );
} finally {
  await runtime.close();
}
