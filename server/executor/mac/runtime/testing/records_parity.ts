/** One private-files sequence and one native-fault worker. No host or provider starts. */
import fs, {
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { FilesService } from '../../files.ts';
import { ExecutorValidationError } from '../../../protocol.ts';
import { atomic_record, read_record } from '../records.ts';

if (!isMainThread) {
  const root = workerData as string;
  const operations = await import('../record_operations.ts');
  const path = join(root, 'record.json');
  writeFileSync(path, 'previous');
  const original = fs.writeSync;
  let failed = false;
  try {
    fs.writeSync = (() => {
      throw new Error('Injected record write failure');
    }) as typeof original;
    syncBuiltinESMExports();
    try {
      operations.atomic_runtime_record(path, 'new');
    } catch (error) {
      failed = (error as Error).message === 'Injected record write failure';
    }
  } finally {
    fs.writeSync = original;
    syncBuiltinESMExports();
  }
  const preserved = readFileSync(path, 'utf8') === 'previous' && readdirSync(root).length === 1;
  let cleanup_preserved = false,
    temporary: string | undefined;
  try {
    fs.writeSync = (() => {
      temporary = join(
        root,
        readdirSync(root).find((name) => name.startsWith('.record.json.'))!,
      );
      fs.unlinkSync(temporary);
      mkdirSync(temporary);
      throw new Error('Injected record write failure');
    }) as typeof original;
    syncBuiltinESMExports();
    try {
      operations.atomic_runtime_record(path, 'new');
    } catch (error) {
      cleanup_preserved =
        error instanceof AggregateError &&
        error.errors[0]?.message === 'Injected record write failure' &&
        error.errors[1]?.syscall === 'unlinkat';
    }
  } finally {
    fs.writeSync = original;
    syncBuiltinESMExports();
    if (temporary !== undefined) fs.rmdirSync(temporary);
  }
  const held = root + '-held',
    foreign = root + '-foreign';
  mkdirSync(foreign);
  writeFileSync(join(foreign, 'record.json'), 'foreign');
  let swapped = false;
  try {
    fs.writeSync = ((...args: Parameters<typeof original>) => {
      if (!swapped) {
        swapped = true;
        renameSync(root, held);
        symlinkSync(foreign, root);
      }
      return Reflect.apply(original, fs, args);
    }) as typeof original;
    syncBuiltinESMExports();
    operations.atomic_runtime_record(path, 'retained');
  } finally {
    fs.writeSync = original;
    syncBuiltinESMExports();
  }
  const contained =
    readFileSync(join(held, 'record.json'), 'utf8') === 'retained' &&
    readFileSync(join(foreign, 'record.json'), 'utf8') === 'foreign' &&
    readdirSync(held).length === 1;
  operations.cleanup_record_handles();
  parentPort!.postMessage({
    failed,
    preserved,
    cleanup_preserved,
    swapped,
    contained,
  });
} else {
  const root = process.argv[2]!,
    began = performance.now();
  const input = JSON.parse(readFileSync(join(root, 'input.json'), 'utf8')) as {
    record: Record<string, unknown>;
  };
  const files = new FilesService();
  const path = join(root, 'updates', 'selection.json');
  const report: Record<string, unknown> = {};
  try {
    report.missing = await read_record(files, path);
    await atomic_record(files, path, input.record);
    report.bytes = readFileSync(path, 'utf8');
    report.mode = fs.statSync(path).mode & 0o777;
    report.value = await read_record(files, path);
    await files.atomic_runtime_record(path, 'null');
    try {
      await read_record(files, path);
    } catch (error) {
      if (!(error instanceof ExecutorValidationError)) throw error;
      report.nonobject = { error: 'validation', message: error.message };
    }
    await files.remove_runtime_record(path);
    report.removed = await read_record(files, path);
  } finally {
    await files.stop({ deadline: performance.now() / 1000 + 10 });
    await files.settled();
  }
  const fault = join(root, 'fault');
  mkdirSync(fault);
  const started = performance.now();
  report.native = await new Promise((resolve, reject) => {
    const worker = new Worker(new URL(import.meta.url), {
      execArgv: [],
      workerData: fault,
    });
    let result: unknown;
    worker.on('message', (value) => (result = value));
    worker.on('error', reject);
    worker.on('exit', (code) =>
      code === 0 && result ? resolve(result) : reject(new Error('Record fault worker failed.')),
    );
  });
  report.native_worker_ms = performance.now() - started;
  report.elapsed_seconds = (performance.now() - began) / 1000;
  console.log(JSON.stringify(report));
}
