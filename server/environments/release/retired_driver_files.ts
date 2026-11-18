/** Empty files that an installed server of an older release still requires. */
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

/**
 * Hermes was deleted, but the installed live server lists these two files in its Hermes driver
 * manifest and refuses a release without them. Remove this list in a later release, after live
 * runs a server that no longer asks for them.
 */
export const RETIRED_DRIVER_FILES = [
  'src/planner/conversation/backends/hermes_acp_extension.py',
  'src/planner/conversation/backends/hermes_model_catalog.py',
] as const;

export async function write_retired_driver_files(root: string): Promise<void> {
  for (const name of RETIRED_DRIVER_FILES) {
    await mkdir(dirname(join(root, name)), { recursive: true });
    await writeFile(join(root, name), '', { flag: 'wx', mode: 0o644 });
  }
}
