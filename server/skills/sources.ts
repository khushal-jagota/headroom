import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Database } from '../core/database/contracts.ts';
import { read_all_skill_sources } from './data.ts';
import { atomic_replace_bytes, with_skill_publication_lock } from './files.ts';
export {
  RETIRED_PANELS_SKILL_NAMES,
  WORKER_ONLY_SKILL_NAMES,
  require_synthetic_destination,
} from './native_publication.ts';
export function write_skill_home(database: Database, parent: string): string {
  return with_skill_publication_lock(parent, (root) => {
    const sources = database.read(read_all_skill_sources);
    for (const [name, source] of Object.entries(sources)) {
      const path = join(root, name, 'SKILL.md'),
        content = Buffer.from(source);
      if (existsSync(path) && readFileSync(path).equals(content)) continue;
      atomic_replace_bytes(path, content);
    }
    return root;
  });
}
