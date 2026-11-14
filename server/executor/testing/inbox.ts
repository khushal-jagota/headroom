/** Continuation of the staged-file proof through canonical publication and receipts. */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { createDatabase } from '../../core/database/index.ts';
import { PlannerError } from '../../core/contracts.ts';
import { InboxStore, MissingFiles } from '../data/inbox.ts';
import { FileStaging } from '../vps_files.ts';
import { decode_value, parse_identity, ExecutorValidationError } from '../protocol.ts';
import type { WireValue } from '../contracts.ts';
const spec = JSON.parse(readFileSync(0, 'utf8')) as unknown as {
  path: string;
  identity: WireValue<'ExecutorIdentity'>;
  envelope: WireValue<'QueuedEnvelope'>;
  missing: WireValue<'FileManifest'>;
};
const database = createDatabase(spec.path),
  store = new InboxStore(database, new FileStaging(spec.path));
const identity = parse_identity(spec.identity),
  envelope = decode_value(spec.envelope, 'QueuedEnvelope');
try {
  const published = await store.publish_file_dependencies(envelope, identity);
  try {
    // JSON object order cannot change the manifest that publication admitted.
    const reordered = {
      ...envelope,
      file_dependencies: (envelope.file_dependencies ?? []).map((manifest) =>
        Object.fromEntries(Object.entries(manifest).reverse()),
      ),
    };
    const results: unknown[] = [];
    const accepted = store.accept(
      decode_value(reordered, 'QueuedEnvelope'),
      (tx) => {
        tx.run('UPDATE executor_child_assignments SET created_at_ms=2');
        return { saved: true };
      },
      101,
      { identity, published },
    );
    results.push(accepted);
    const no_replay = (): never => {
      throw new Error('replay ran its writer');
    };
    results.push(store.accept(envelope, no_replay, 102, { identity }));
    results.push(
      store.accept({ ...envelope, sequence: 1, submission_id: 'older' }, no_replay, 103, {
        identity,
      }),
    );
    try {
      store.accept({ ...envelope, submission_id: 'conflict' }, no_replay, 104, { identity });
    } catch (error) {
      if (!(error instanceof PlannerError)) throw error;
      results.push(error.toPayload());
    }
    results.push(
      store.accept(
        { ...envelope, sequence: 3, submission_id: 'refused' },
        (tx) => {
          tx.run('UPDATE executor_child_assignments SET created_at_ms=3');
          throw new ExecutorValidationError('domain refused');
        },
        105,
        { identity, published },
      ),
    );
    try {
      await store.publish_file_dependencies(
        { ...envelope, sequence: 4, submission_id: 'missing', file_dependencies: [spec.missing] },
        identity,
      );
    } catch (error) {
      if (!(error instanceof MissingFiles)) throw error;
      results.push({ missing: error.manifests });
    }
    const root = dirname(spec.path),
      files_root = join(root, 'files'),
      files: Record<string, string> = {};
    function walk(path: string): void {
      for (const entry of readdirSync(path, { withFileTypes: true }).sort((a, b) =>
        a.name.localeCompare(b.name),
      )) {
        const location = join(path, entry.name);
        if (entry.isDirectory()) walk(location);
        else files[relative(files_root, location)] = readFileSync(location, 'utf8');
      }
    }
    walk(files_root);
    process.stdout.write(
      JSON.stringify(
        database.read((sql) => ({
          results,
          positions: sql.all(
            'SELECT sender_id,conversation_id,sequence,submission_id,disposition,result_json FROM mac_inbox_positions ORDER BY sender_id,conversation_id',
          ),
          assignments: sql.all(
            'SELECT * FROM executor_child_assignments ORDER BY executor_incarnation,child_id',
          ),
          files,
          staged: readdirSync(join(root, 'executor-uploads')).sort(),
        })),
      ),
    );
  } finally {
    await published.close();
  }
} finally {
  database.close();
}
