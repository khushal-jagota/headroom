/** One private build request uses the existing publication lifetime and child owner. */
import { isAbsolute } from 'node:path';
import { with_publication_entry } from '../release/publication_entry.ts';
import { publication_json_file, publication_text } from '../release/publication_identity.ts';
import { validate_app_sha } from '../release/logic/manifest.ts';
import { is_record } from '../../core/records.ts';
import { build_linux_executor } from './build.ts';
import type { LinuxExecutorBuildOptions } from './contracts.ts';

try {
  process.exitCode = await with_publication_entry(async (execution, register_output) => {
    if (process.argv.length !== 3) throw new Error('Expected one Linux executor build request');
    const value = await publication_json_file(process.argv[2]!);
    if (
      !is_record(value) ||
      Object.keys(value).sort().join(',') !== 'output_directory,requested_sha,source_root'
    )
      throw new Error('Invalid Linux executor build request');
    const options: LinuxExecutorBuildOptions = {
      source_root: publication_text(value.source_root, 'source_root'),
      requested_sha: validate_app_sha(publication_text(value.requested_sha, 'requested_sha')),
      output_directory: publication_text(value.output_directory, 'output_directory'),
    };
    if (![options.source_root, options.output_directory].every(isAbsolute))
      throw new Error('Linux executor build paths must be absolute');
    execution.signal.throwIfAborted();
    await build_linux_executor(options, execution);
    register_output(options.output_directory);
  });
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
