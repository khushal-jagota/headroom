/** Private one-request entry. The host must isolate its environment before Node starts. */
import { isAbsolute } from 'node:path';
import { build_linux_publication } from './publication.ts';
import { with_publication_entry } from './publication_entry.ts';
import { publication_json_file, publication_text, source_run } from './publication_identity.ts';
import { validate_app_sha } from './logic/manifest.ts';
import { is_object } from '../updates/codec.ts';
import type { LinuxPublicationOptions } from './publication_contracts.ts';

try {
  process.exitCode = await with_publication_entry(async (execution, register_output) => {
    if (process.argv.length !== 3) throw new Error('Expected one publication build request file');
    const request = await publication_json_file(process.argv[2]!);
    if (
      !is_object(request) ||
      Object.keys(request).sort().join(',') !==
        'output_directory,public_cli_entrypoint,requested_sha,source_root'
    )
      throw new Error('Invalid publication build request');
    const options: LinuxPublicationOptions = {
      source_root: publication_text(request.source_root, 'source_root'),
      requested_sha: validate_app_sha(publication_text(request.requested_sha, 'requested_sha')),
      output_directory: publication_text(request.output_directory, 'output_directory'),
    };
    for (const path of [options.source_root, options.output_directory])
      if (!isAbsolute(path)) throw new Error('Publication paths must be absolute');
    const dependencies = {
      public_cli_entrypoint: publication_text(
        request.public_cli_entrypoint,
        'public_cli_entrypoint',
      ),
    };
    const provenance = await source_run(options.requested_sha);
    execution.signal.throwIfAborted();
    const result = await build_linux_publication(options, dependencies, provenance, execution);
    if (result === undefined) throw new Error('Publication build produced no result');
    register_output(options.output_directory);
  });
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
