import type { ReadSql } from '../../core/database/contracts.ts';
import type { CurrentAuthority, CurrentViewSnapshot, ViewRequest } from '../../../web/src/lib/navigation/contracts.ts';
import { digest, ORDERING_GENERATION } from '../logic/json.ts';
import { viewIdentity } from '../logic/view.ts';
import { readContent } from '../views/read.ts';
export function snapshot(sql: ReadSql, value: ViewRequest, authority: CurrentAuthority): CurrentViewSnapshot {
  const [coverage, content] = readContent(sql, value);
  return { event: 'snapshot', wire_version: 3, projection_version: 7, view_id: viewIdentity(value), scope_generation: value.scope_generation, ordering_generation: ORDERING_GENERATION, coverage, content_version: digest(content), mode: 'current', authority, content };
}
