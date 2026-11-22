import type { ReviewAskItem } from '../navigation/contracts';
import { workspaceAddress } from '../workspaceAddress';

/** The conversation the ask was raised in, where it can also be answered. */
export function reviewAskHref(item: ReviewAskItem): string {
  const owner = item.owner_kind === 'chief' ? { kind: 'chief' as const } : { kind: item.owner_kind, id: item.owner_id };
  return workspaceAddress(owner, undefined, undefined, item.conversation_id);
}
