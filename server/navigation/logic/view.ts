import type { ViewRequest, ViewWindowRequest } from '../../../web/src/lib/navigation/contracts.ts';
import { compact, digest } from './json.ts';
const sections = ['rail_parents', 'rail_tickets', 'children', 'settled', 'unread', 'review', 'goals'];
const groups = ['all', 'awaiting_approval', 'awaiting_answer', 'assigned', 'awaiting_reply', 'errored', 'backlog', 'agent', 'waiting_to_closeout', 'status_awaiting_approval', 'empty', 'blocked', 'done'];
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function fields(value: unknown, names: string[]): value is Record<string, unknown> { return object(value) && compact(Object.keys(value).sort()) === compact([...names].sort()); }
function text(value: unknown): value is string { return typeof value === 'string' && [...value].length > 0 && [...value].length <= 256; }
function owner(value: unknown): boolean { return value === null || fields(value, ['kind', 'id']) && ['chief', 'manager', 'ticket', 'goal'].includes(String(value.kind)) && text(value.id) && (value.kind !== 'chief' || value.id === 'chief'); }
function fail(message: string): never { throw new Error(message); }
export function windowKey(window: ViewWindowRequest): string { return compact([window.section, window.owner === null ? null : [window.owner.kind, window.owner.id], window.group, window.child_filter]); }
export function viewIdentity(value: ViewRequest): string { return digest({ ...value, scope_generation: 0, windows: [...value.windows].sort((a, b) => windowKey(a) < windowKey(b) ? -1 : windowKey(a) > windowKey(b) ? 1 : 0) }); }
export function parseView(input: unknown): ViewRequest {
  if (!fields(input, ['projection_version', 'scope_generation', 'locale', 'selected', 'selected_conversation_id', 'review_conversation_ids', 'review_skipped_keys', 'review_priority_keys', 'windows'])) fail('invalid view request fields');
  if (input.projection_version !== 7 || !Number.isSafeInteger(input.scope_generation) || Number(input.scope_generation) < 0) fail('invalid view generation');
  if (!['en-US', 'de-DE'].includes(String(input.locale))) fail('unsupported navigation ordering locale');
  if (!owner(input.selected) || input.selected_conversation_id !== null && !text(input.selected_conversation_id)) fail('invalid selected owner');
  if (!Array.isArray(input.review_conversation_ids) || input.review_conversation_ids.length > 256 || input.review_conversation_ids.some(item => !text(item)) || new Set(input.review_conversation_ids).size !== input.review_conversation_ids.length) fail('invalid Review conversation context');
  for (const name of ['review_skipped_keys', 'review_priority_keys']) {
    const items = input[name];
    if (!Array.isArray(items) || items.some(item => !fields(item, ['kind', 'id']) || !['ticket', 'conversation'].includes(String(item.kind)) || !text(item.id))) fail('invalid Review history');
    if (new Set(items.map(item => compact(item))).size !== items.length) fail('duplicate Review history key');
  }
  if (!Array.isArray(input.windows) || input.windows.length > 160) fail('invalid view windows');
  const seen = new Set<string>();
  let total = input.review_conversation_ids.length;
  for (const window of input.windows) {
    if (!fields(window, ['section', 'owner', 'group', 'child_filter', 'anchor', 'direction', 'limit'])) fail('invalid window fields');
    if (!sections.includes(String(window.section)) || !groups.includes(String(window.group)) || !['all', 'active', 'backlogged', 'done'].includes(String(window.child_filter)) || !owner(window.owner)) fail('invalid window membership');
    if (!['before', 'after'].includes(String(window.direction)) || !Number.isInteger(window.limit) || Number(window.limit) < 0 || Number(window.limit) > 60) fail('invalid window bounds');
    if (window.anchor !== null && (typeof window.anchor !== 'string' || window.anchor.length > 8192)) fail('invalid window anchor');
    if (['rail_tickets', 'children'].includes(String(window.section)) && (!object(window.owner) || !['chief', 'manager'].includes(String(window.owner.kind)))) fail('window requires a parent');
    if (['rail_parents', 'settled', 'review'].includes(String(window.section)) && window.owner !== null) fail('global window has no parent');
    if ((!['children', 'rail_tickets'].includes(String(window.section)) && window.group !== 'all') || (window.section !== 'children' && window.child_filter !== 'all')) fail('unsupported window filter');
    const identity = windowKey(window as unknown as ViewWindowRequest);
    if (seen.has(identity)) fail('duplicate view window');
    seen.add(identity); total += Number(window.limit);
  }
  if (total > 256) fail('view request exceeds row budget');
  return input as unknown as ViewRequest;
}
