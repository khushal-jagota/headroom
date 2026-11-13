/** Final fixed entry. Rehearsal success is enforced by the publisher's real job needs. */
import { is_object } from '../updates/codec.ts';
import { with_publication_entry } from './publication_entry.ts';
import {
  publication_id,
  publication_json_file,
  publication_text,
  require_publication_run,
  source_run,
} from './publication_identity.ts';
import { write_publication_receipt } from './publication_receipt.ts';

try {
  process.exitCode = await with_publication_entry(async (execution, register_output) => {
    if (process.argv.length !== 3) throw new Error('Expected one publication receipt request file');
    const request = await publication_json_file(process.argv[2]!);
    if (
      !is_object(request) ||
      Object.keys(request).sort().join(',') !==
        'artifact_id,linux_root,mac_artifact_id,mac_build_result,mac_root,output_directory,source_root'
    )
      throw new Error('Invalid publication receipt request');
    const provenance = await source_run(publication_text(process.env.GITHUB_SHA, 'GITHUB_SHA'));
    require_publication_run(provenance);
    if (process.env.GITHUB_WORKFLOW_SHA !== provenance.source_sha)
      throw new Error('Publication workflow source differs from the selected SHA');
    const output_directory = publication_text(request.output_directory, 'output_directory');
    await write_publication_receipt(
      {
        source_root: publication_text(request.source_root, 'source_root'),
        linux_root: publication_text(request.linux_root, 'linux_root'),
        mac_root: publication_text(request.mac_root, 'mac_root'),
        output_directory,
        artifact_id: publication_id(request.artifact_id, 'artifact ID'),
        mac_artifact_id: publication_id(request.mac_artifact_id, 'Mac artifact ID'),
        mac_build_result: publication_text(request.mac_build_result, 'Mac build result'),
      },
      provenance,
      execution,
    );
    register_output(output_directory);
  });
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
