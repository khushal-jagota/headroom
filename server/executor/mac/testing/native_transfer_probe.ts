/** Run the released Mac file worker and HTTP transfer code with the package's Node. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import type { FileUploadRequest } from '../../wire.generated.ts';

const input = JSON.parse(await readFile(process.argv[2]!, 'utf8')) as {
  runtime: string;
  root: string;
  origin: string;
  identity: ExecutorIdentity;
  credential_path: string;
  sha256: string;
  byte_count: number;
};
const load = (path: string) => import(pathToFileURL(join(input.runtime, path)).href);
const { FilesService } = await load('server/executor/mac/files.ts');
const { MacHttpClient } = await load('server/executor/mac/transport/http.ts');
const { MacFileTransport } = await load('server/executor/mac/transport/files.ts');
const files = new FilesService();
const client = new MacHttpClient(input.origin, 'test-executor', input.identity);
const transport = new MacFileTransport(client, files);
try {
  await client.ensure_credential(input.credential_path);
  const target = join(input.root, 'downloaded.bin');
  const digest = await transport.download_command_file(
    'rehearsal-command',
    'rehearsal-file',
    target,
  );
  const bytes = await readFile(target);
  assert.equal(bytes.length, input.byte_count);
  assert.equal(digest, input.sha256);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), input.sha256);
  assert.deepEqual(await files.file_digest(target), [input.sha256, input.byte_count]);
  const source = join(input.root, 'upload.bin');
  await writeFile(source, bytes, { flag: 'wx' });
  const upload: FileUploadRequest = {
    child: {
      executor_incarnation: input.identity.executor_incarnation,
      child_id: 'rehearsal-child',
      conversation_id: 'rehearsal-files',
    },
    manifest: {
      scope: 'ticket',
      owner_id: 'rehearsal-ticket',
      relative_path: 'artifacts/rehearsal.bin',
      sha256: input.sha256,
      byte_count: input.byte_count,
    },
  };
  await transport.upload_file(upload, source);
  process.stdout.write(
    JSON.stringify({
      packaged_mac_transfer: 'passed',
      sha256: input.sha256,
      byte_count: input.byte_count,
    }) + '\n',
  );
} finally {
  const options = { deadline: performance.now() / 1000 + 30 };
  const outcomes = await Promise.allSettled([client.stop(options), files.stop(options)]);
  await Promise.all([client.settled(), files.settled()]);
  const failures = outcomes.filter((outcome) => outcome.status === 'rejected');
  if (failures.length)
    throw new AggregateError(
      failures.map((failure) => failure.reason),
      'Native transfer cleanup failed',
    );
}
