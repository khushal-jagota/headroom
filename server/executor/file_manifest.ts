/** A manifest's semantic identity does not depend on JSON member order. */
import { as_object } from './protocol.ts';
import type { FileManifest } from './wire.generated.ts';

export function file_manifest_key(manifest: FileManifest): string {
  // Keep the existing staging filename's alphabetic five-field hash input.
  return JSON.stringify(
    as_object(
      {
        byte_count: manifest.byte_count,
        owner_id: manifest.owner_id,
        relative_path: manifest.relative_path,
        scope: manifest.scope,
        sha256: manifest.sha256,
      },
      'FileManifest',
    ),
  );
}
