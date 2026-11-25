/** The owner's Review queue as one plain read, from the same window the browser shows. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { NavigationUnavailable } from '../logic/errors.ts';
import { VIEW_CONFIG } from './configuration.ts';
import { eligibleForOwnerReview, sectionKey } from './logic.ts';
import { askWaitingSince } from './projection.ts';
import { continuity } from './read.ts';
import { record, reviewMessageItems } from './review.ts';

export type ReviewWait = 'proposal' | 'message' | 'question';
export interface ReviewCard {
  kind: 'ticket' | 'manager' | 'chief';
  id: string;
  title: string;
  waits: ReviewWait[];
  stage: string | null;
  /** Unix seconds of the card's oldest item. Null for an ask that holds no time. */
  waiting_since: number | null;
}
export type ReviewBody =
  | { available: true; total: number; cards: ReviewCard[] }
  | { available: false; total: null; cards: [] };

function card(sql: ReadSql, kind: string, id: string, rank: number): ReviewCard {
  const waiting_since = rank === Number.MAX_SAFE_INTEGER ? null : rank;
  const value = record(sql, [kind, id]);
  if (value === null) throw new NavigationUnavailable('review member record is absent');
  const waits: ReviewWait[] = [];
  if (kind === 'ticket') {
    if (!('integrity_error' in value) && eligibleForOwnerReview(value)) waits.push('proposal');
    if (reviewMessageItems(sql, id).length) waits.push('message');
    if (askWaitingSince(sql, 'ticket', value, value.conversation_id) !== null)
      waits.push('question');
    return { kind, id, title: String(value.title ?? ''), waits, stage: value.stage, waiting_since };
  }
  // A Manager or Chief card is its canonical conversation address.
  const owner = record(sql, [value.owner_kind, value.owner_id]);
  const conversation = record(sql, ['conversation', value.conversation_id]);
  if (owner === null || conversation === null)
    throw new NavigationUnavailable('review member owner is absent');
  if (conversation.attention.durable.message_for_owner) waits.push('message');
  if (askWaitingSince(sql, value.owner_kind, owner, value.conversation_id) !== null)
    waits.push('question');
  return {
    kind: value.owner_kind,
    id: value.owner_id,
    title: String(owner.title ?? owner.label ?? ''),
    waits,
    stage: null,
    waiting_since,
  };
}

/** Oldest first, every card. An untrustworthy index reads as unavailable, never as empty. */
export function review_body(sql: ReadSql): ReviewBody {
  try {
    continuity(sql);
    const group = [sectionKey('review', null), 'current', VIEW_CONFIG.locales[0]];
    const total =
      sql.get<{ value: number }>(
        'SELECT value FROM navigation_view_counts WHERE section_key=? AND profile=? AND locale=?',
        group,
      )?.value ?? 0;
    const members = sql.all<{ kind: string; id: string; rank: number }>(
      'SELECT kind,id,rank FROM navigation_view_ordered WHERE section_key=? AND profile=? AND locale=? ORDER BY position',
      group,
    );
    if (members.length !== total)
      throw new NavigationUnavailable('navigation ordered projection is incomplete');
    const cards = members.map((member) => card(sql, member.kind, member.id, member.rank));
    return { available: true, total, cards };
  } catch (error) {
    // As the snapshot route does, an absent or unfinished index is unavailable, not a fault.
    const unprojected =
      error instanceof NavigationUnavailable ||
      (error instanceof Error &&
        (error.constructor === Error ||
          String((error as Error & { code?: string }).code ?? '').startsWith('SQLITE_')));
    if (!unprojected) throw error;
    return { available: false, total: null, cards: [] };
  }
}
