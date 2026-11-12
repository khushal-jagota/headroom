/** Incremental memberships and aggregates, inside the canonical source transaction. */
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { NavigationRecord, RecordKey, RecordKind } from '../../../web/src/lib/navigation/contracts.ts';
import { compact, digest, ORDERING_GENERATION } from '../logic/json.ts';
import type { Fact, Key, Membership, ProjectionMetadata } from './contracts.ts';
import { VIEW_CONFIG } from './configuration.ts';
import { GROUPS, eligibleForOwnerReview, group, messageOwnerIsLive, project, sectionKey } from './logic.ts';
import { binaryCompare, sortKey } from './ordering.ts';
import { canonicalReviewAddress, record, reviewMessageItems } from './review.ts';
export { record } from './review.ts';
import { prepareOrdered, reorderGroup } from './ordered.ts';
import type { OrderedGroup } from './ordered.ts';

const keyString = (key: Key): string => compact(key);
function attention(sql: ReadSql, conversation: string | null | undefined): Fact | null {
  return conversation ? record(sql, ['conversation', conversation])?.attention ?? null : null;
}
function metadata(sql: ReadSql, key: Key, value: Fact, content_hash: string): ProjectionMetadata {
  const [kind, id] = key;
  const conversation_id = value.conversation_id ?? null;
  let parent: readonly [string | null, string | null] = kind === 'ticket'
    ? value.manager_kind === 'chief' ? ['chief', 'chief'] : ['manager', value.manager_id]
    : [value.parent_kind ?? null, value.parent_id ?? null];
  if (kind === 'goal') parent = [value.holder_kind, value.holder_kind === 'manager' ? value.holder_id : 'chief'];
  const knowledge = kind === 'conversation' ? value.attention : attention(sql, conversation_id);
  return {
    kind, id, content_hash, conversation_id, parent_kind: parent[0], parent_id: parent[1],
    owner_kind: value.owner_kind ?? null, owner_id: value.owner_id ?? null, project_id: value.project_id ?? null,
    working: Number(!!knowledge?.current?.signal.working), out_claim: Number(!!value.worker_step_claim_out),
    review: Number(kind === 'ticket' && !('integrity_error' in value) && eligibleForOwnerReview(value)),
    unread: Number(kind === 'address' && !!knowledge?.durable.message_for_owner && record(sql, [value.owner_kind, value.owner_id]) !== null),
    settled: Number(!!value.settled),
  };
}
function members(sql: ReadSql, key: Key, value: Fact, metadata: ProjectionMetadata): Membership[] {
  const [kind, id] = key;
  const result = new Map<string, Membership>();
  const parent: Key = [String(metadata.parent_kind), String(metadata.parent_id)];
  const knowledge = attention(sql, value.conversation_id);
  const messages = kind === 'ticket' ? reviewMessageItems(sql, id) : [];
  const signal = knowledge?.current ?? null;
  for (const locale of VIEW_CONFIG.locales) {
    const title = sortKey(String(value.title ?? ''), 'base');
    const identityKey = sortKey(id, 'variant');
    function add(section: string, owner: Key | null, grouping = 'all', childFilter = 'all', rank = 0, title_key = title, id_key = identityKey): void {
      const row: Membership = { section_key: sectionKey(section, owner, grouping, childFilter), profile: 'current', locale, kind: kind as RecordKind, id, rank, title_key, id_key };
      result.set(memberIdentity(row), row);
    }
    if (kind === 'goal' && value.active) {
      add('goals', parent, 'all', 'all', Number(value.created_at), sortKey('', 'binary'));
    } else if (kind === 'manager') {
      add(value.settled ? 'settled' : 'rail_parents', null, 'all', 'all', value.settled ? -Number(value.updated_at) : Number(value.created_at), sortKey('', 'binary'));
    } else if (kind === 'ticket') {
      const broken = 'integrity_error' in value;
      const childFilter = broken ? 'active' : value.stage === 'done' ? 'done' : value.is_backlogged ? 'backlogged' : 'active';
      const childGroup = group(value, signal, true);
      const rank = parent[0] === 'chief' ? Number(value.created_at) : -Number(value.updated_at);
      const childTitle = parent[0] === 'chief' ? sortKey('', 'binary') : title;
      const childId = parent[0] === 'chief' ? sortKey(id, 'binary') : identityKey;
      if (broken || parent[0] !== 'chief' || childFilter === 'done' || !value.terminal) {
        for (const filtering of ['all', childFilter]) for (const grouping of ['all', childGroup]) {
          const grouped = parent[0] !== 'chief' && filtering === 'active' && grouping === 'all';
          add('children', parent, grouping, filtering,
            grouped ? GROUPS.indexOf(childGroup as typeof GROUPS[number]) : rank,
            grouped ? sortKey(String(value.title ?? ''), 'base', -Number(value.updated_at)) : childTitle,
            childId);
        }
      }
      const parentValue = record(sql, parent);
      const eligible = broken || !value.is_backlogged && !value.blocked && value.ticket_status !== 'blocked'
        && value.stage !== 'done' && !parentValue?.settled;
      if (eligible) {
        const railGroup = group(value, signal, true);
        for (const grouping of ['all', railGroup]) {
          add('rail_tickets', parent, grouping, 'all', grouping === 'all' ? GROUPS.indexOf(railGroup as typeof GROUPS[number]) : 0,
            sortKey(String(value.title ?? ''), 'base', -Number(value.updated_at)));
        }
      }
      if (metadata.review || messages.length) {
        const waiting = messages.map(message => Number(message.waiting_since));
        if (metadata.review) waiting.push(Number(value.proposal_created_at));
        add('review', null, 'all', 'all', Math.min(...waiting), sortKey('ticket', 'binary'), sortKey(id, 'binary'));
      }
    } else if (kind === 'address' && metadata.unread) {
      const conversationId = String(value.conversation_id);
      const ownerValue = record(sql, [value.owner_kind, value.owner_id]);
      if (ownerValue && value.owner_kind !== 'ticket' && messageOwnerIsLive(value.owner_kind, ownerValue)
          && canonicalReviewAddress(sql, conversationId) === id) {
        const conversation = record(sql, ['conversation', conversationId]);
        if (!conversation) throw new Error('unread navigation conversation is absent');
        add('review', null, 'all', 'all', Number(conversation.first_unread_owner_message_at), sortKey('message', 'binary'), sortKey(conversationId, 'binary'));
      }
      add('unread', null, 'all', 'all', -Number(!!value.current), sortKey('', 'binary'));
      const owner: Key = [value.owner_kind, value.owner_id];
      if (owner[0] === 'ticket') add('unread', owner, 'all', 'all', -Number(!!value.current), sortKey('', 'binary'));
      const ownerRecord = record(sql, owner);
      if (value.parent_kind != null && keyString(parent) !== keyString(owner) && !ownerRecord?.is_backlogged) {
        add('unread', parent, 'all', 'all', -Number(!!value.current), sortKey('', 'binary'));
      }
    }
  }
  return [...result.values()];
}
function memberIdentity(row: Membership): string {
  return compact([row.section_key, row.profile, row.locale, row.kind, row.id, row.rank, row.title_key.toString('hex'), row.id_key.toString('hex')]);
}
function globalDelta(tx: TransactionSql, name: string, amount: number): void {
  if (!amount) return;
  if (amount > 0) tx.run('INSERT INTO navigation_view_globals(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value', [name, amount]);
  else tx.run('UPDATE navigation_view_globals SET value=value+? WHERE name=?', [amount, name]);
}
function countDelta(tx: TransactionSql, row: Membership, amount: number): void {
  if (amount > 0) tx.run('INSERT INTO navigation_view_counts(section_key,profile,locale,value) VALUES(?,?,?,?) ON CONFLICT(section_key,profile,locale) DO UPDATE SET value=value+excluded.value', [row.section_key, row.profile, row.locale, amount]);
  else tx.run('UPDATE navigation_view_counts SET value=value+? WHERE section_key=? AND profile=? AND locale=?', [amount, row.section_key, row.profile, row.locale]);
}
function prepared(sql: ReadSql): boolean { return !!sql.get("SELECT 1 FROM sqlite_master WHERE name='navigation_view_meta'"); }
export function updateProjection(tx: TransactionSql, upserts: readonly NavigationRecord[], deletes: readonly Pick<RecordKey, 'kind' | 'id'>[], initializing = false): void {
  if (!prepared(tx)) return;
  const continuity = tx.get<{epoch: string; revision: number}>('SELECT epoch,revision FROM navigation_meta')!;
  const meta = tx.get<{epoch: string; ordering_generation: string}>('SELECT epoch,ordering_generation FROM navigation_view_meta');
  if (!initializing && (!meta || meta.epoch !== continuity.epoch || meta.ordering_generation !== ORDERING_GENERATION)) return;
  const changed = new Map<string, Key>();
  for (const item of deletes) if (item.kind !== 'totals') changed.set(keyString([item.kind, item.id]), [item.kind, item.id]);
  for (const item of upserts) {
    if (item.kind === 'totals') continue;
    const old = tx.get<{content_hash: string}>('SELECT content_hash FROM navigation_view_index WHERE kind=? AND id=?', [item.kind, item.id]);
    if (!old || old.content_hash !== digest(project(item))) changed.set(keyString([item.kind, item.id]), [item.kind, item.id]);
  }
  const affected = new Map(changed);
  function affect(rows: {kind: string; id: string}[]): void {
    for (const row of rows) affected.set(keyString([row.kind, row.id]), [row.kind, row.id]);
  }
  for (const [kind, id] of changed.values()) {
    if (kind === 'conversation') affect(tx.all('SELECT kind,id FROM navigation_view_index WHERE conversation_id=? ORDER BY kind,id', [id]));
    if (['ticket', 'manager', 'chief'].includes(kind)) affect(tx.all('SELECT kind,id FROM navigation_view_index WHERE owner_kind=? AND owner_id=? ORDER BY kind,id', [kind, id]));
    if (kind === 'manager') affect(tx.all("SELECT kind,id FROM navigation_view_index WHERE parent_kind='manager' AND parent_id=? ORDER BY kind,id", [id]));
  }
  const addressConversations = new Set<string>();
  for (const key of affected.values()) {
    if (key[0] !== 'address') continue;
    const value = record(tx, key);
    const old = tx.get<{conversation_id: string | null}>('SELECT conversation_id FROM navigation_view_index WHERE kind=? AND id=?', [...key]);
    if (value) addressConversations.add(String(value.conversation_id));
    if (old?.conversation_id) addressConversations.add(old.conversation_id);
  }
  for (const id of addressConversations) {
    affect(tx.all<{kind: string; id: string}>("SELECT kind,id FROM navigation_records WHERE kind='address' AND json_extract(value,'$.conversation_id')=? ORDER BY id", [id]));
  }
  for (const key of [...affected.values()]) {
    if (key[0] !== 'address') continue;
    const value = record(tx, key);
    const old = tx.get<{owner_kind: string | null; owner_id: string | null}>('SELECT owner_kind,owner_id FROM navigation_view_index WHERE kind=? AND id=?', [...key]);
    if (old?.owner_kind === 'ticket' && old.owner_id) affected.set(keyString(['ticket', old.owner_id]), ['ticket', old.owner_id]);
    if (value?.owner_kind === 'ticket') affected.set(keyString(['ticket', value.owner_id]), ['ticket', value.owner_id]);
  }
  const sections = new Set<string>();
  const orderedGroups = new Map<string, OrderedGroup>();
  function reorder(row: Membership): void {
    const group: OrderedGroup = [row.section_key, row.profile, row.locale];
    orderedGroups.set(compact(group), group);
  }
  const pointerIds = new Set([...changed.values()].filter(([kind]) => kind === 'conversation').map(([, id]) => id));
  let globalsChanged = false;
  const sorted = [...affected.values()].sort(([ak, ai], [bk, bi]) => binaryCompare(ak, bk) || binaryCompare(ai, bi));
  for (const key of sorted) {
    const old = tx.get<ProjectionMetadata>('SELECT * FROM navigation_view_index WHERE kind=? AND id=?', [...key]) ?? null;
    const value = record(tx, key);
    const fresh = value === null ? null : metadata(tx, key, value, digest(project({kind: key[0], id: key[1], value} as NavigationRecord)));
    for (const candidate of [old, fresh]) if (candidate && ['ticket', 'manager', 'chief', 'goal', 'address'].includes(candidate.kind) && candidate.conversation_id) pointerIds.add(candidate.conversation_id);
    const oldMembers = new Map(tx.all<Membership>('SELECT * FROM navigation_view_memberships WHERE kind=? AND id=? ORDER BY section_key,profile,locale', [...key]).map(row => [memberIdentity(row), row]));
    const newMembers = new Map((fresh && value ? members(tx, key, value, fresh) : []).map(row => [memberIdentity(row), row]));
    const reviewSection = sectionKey('review', null);
    if ([...oldMembers.values(), ...newMembers.values()].some(row => row.section_key === reviewSection)) sections.add(reviewSection);
    for (const [identity, row] of oldMembers) if (!newMembers.has(identity)) {
      tx.run('DELETE FROM navigation_view_memberships WHERE section_key=? AND profile=? AND locale=? AND kind=? AND id=?', [row.section_key, row.profile, row.locale, row.kind, row.id]);
      countDelta(tx, row, -1); sections.add(row.section_key); reorder(row);
    }
    for (const [identity, row] of newMembers) if (!oldMembers.has(identity)) {
      tx.run('INSERT OR REPLACE INTO navigation_view_memberships VALUES(?,?,?,?,?,?,?,?)', [row.section_key, row.profile, row.locale, row.kind, row.id, row.rank, row.title_key, row.id_key]);
      countDelta(tx, row, 1); sections.add(row.section_key); reorder(row);
    }
    for (const [name, field] of [['out_claims', 'out_claim'], ['settled', 'settled']] as const) {
      const difference = (fresh?.[field] ?? 0) - (old?.[field] ?? 0);
      globalDelta(tx, name, difference); globalsChanged ||= difference !== 0;
    }
    if (fresh) tx.run('INSERT OR REPLACE INTO navigation_view_index VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [fresh.kind, fresh.id, fresh.content_hash, fresh.conversation_id, fresh.parent_kind, fresh.parent_id, fresh.owner_kind, fresh.owner_id, fresh.project_id, fresh.working, fresh.out_claim, fresh.review, fresh.unread, fresh.settled]);
    else tx.run('DELETE FROM navigation_view_index WHERE kind=? AND id=?', [...key]);
  }
  for (const group of orderedGroups.values()) reorderGroup(tx, group);
  for (const id of pointerIds) {
    const oldUnread = tx.get<{reference_count: number}>('SELECT reference_count FROM navigation_view_unread_conversations WHERE conversation_id=?', [id]);
    const unread = tx.get<{count: number}>("SELECT count(*) AS count FROM navigation_view_index WHERE conversation_id=? AND kind='address' AND unread=1", [id])!.count;
    const unreadDelta = Number(!!unread) - Number(!!oldUnread);
    globalDelta(tx, 'unread', unreadDelta); globalsChanged ||= unreadDelta !== 0;
    if (unread) tx.run('INSERT OR REPLACE INTO navigation_view_unread_conversations VALUES(?,?)', [id, unread]);
    else tx.run('DELETE FROM navigation_view_unread_conversations WHERE conversation_id=?', [id]);
    const old = tx.get<{reference_count: number; working: number}>('SELECT reference_count,working FROM navigation_view_current_conversations WHERE conversation_id=?', [id]);
    const references = tx.get<{count: number}>("SELECT count(*) AS count FROM navigation_view_index WHERE conversation_id=? AND kind IN ('chief','ticket','manager','goal')", [id])!.count;
    const current = tx.get<{working: number}>("SELECT working FROM navigation_view_index WHERE kind='conversation' AND id=?", [id]);
    const working = Number(!!current?.working);
    const difference = (references ? working : 0) - (old?.working ?? 0);
    globalDelta(tx, 'running_conversations', difference); globalsChanged ||= difference !== 0;
    if (references) tx.run('INSERT OR REPLACE INTO navigation_view_current_conversations VALUES(?,?,?)', [id, references, working]);
    else tx.run('DELETE FROM navigation_view_current_conversations WHERE conversation_id=?', [id]);
  }
  const review = tx.get<{value: number}>("SELECT value FROM navigation_view_counts WHERE section_key=? AND profile='current' AND locale=?", [sectionKey('review', null), VIEW_CONFIG.locales[0]])?.value ?? 0;
  const oldReview = tx.get<{value: number}>("SELECT value FROM navigation_view_globals WHERE name='review'");
  globalsChanged ||= oldReview === undefined || oldReview.value !== review;
  tx.run("INSERT OR REPLACE INTO navigation_view_globals VALUES('review',?)", [review]);
  tx.run('INSERT OR REPLACE INTO navigation_view_changes VALUES(?,?)', [continuity.revision, compact({ keys: [...changed.values()].sort(([ak, ai], [bk, bi]) => binaryCompare(ak, bk) || binaryCompare(ai, bi)), sections: [...sections].sort(binaryCompare), globals: globalsChanged })]);
  tx.run('DELETE FROM navigation_view_changes WHERE revision<?', [Math.max(0, continuity.revision - VIEW_CONFIG.retained_revisions)]);
  tx.run('INSERT OR REPLACE INTO navigation_view_meta VALUES(1,1,?,?,?)', [continuity.epoch, continuity.revision, ORDERING_GENERATION]);
}
export function rebuildProjection(tx: TransactionSql): void {
  if (!prepared(tx) || tx.get<{schema_version: number}>('SELECT schema_version FROM navigation_meta')?.schema_version !== 10) return;
  prepareOrdered(tx);
  tx.run('DELETE FROM navigation_view_ordered');
  for (const suffix of ['meta', 'index', 'memberships', 'counts', 'current_conversations', 'unread_conversations', 'globals', 'changes']) tx.run(`DELETE FROM navigation_view_${suffix}`);
  for (const name of ['running_conversations', 'out_claims', 'review', 'unread', 'settled']) tx.run('INSERT INTO navigation_view_globals VALUES(?,0)', [name]);
  const records = tx.all<{kind: string; id: string; value: string}>("SELECT kind,id,value FROM navigation_records WHERE kind!='totals' ORDER BY kind,id")
    .map(row => ({ kind: row.kind, id: row.id, value: JSON.parse(row.value) }) as NavigationRecord);
  updateProjection(tx, records, [], true);
}
