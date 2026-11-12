import type { ViewRequest, ViewWindowRequest } from '../../../web/src/lib/navigation/contracts.ts';
import { compact, digest } from './json.ts';
import { VIEW_CONFIG } from '../views/configuration.ts';
import { binaryCompare } from '../views/ordering.ts';
const sections = ['rail_parents', 'rail_tickets', 'children', 'settled', 'unread', 'review', 'goals'];
const groups = ['all', 'awaiting_approval', 'awaiting_answer', 'assigned', 'awaiting_reply', 'errored', 'backlog', 'agent', 'waiting_to_closeout', 'status_awaiting_approval', 'empty', 'blocked', 'done'];
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function fields(value: unknown, names: string[]): value is Record<string, unknown> { return object(value) && compact(Object.keys(value).sort()) === compact([...names].sort()); }
function text(value: unknown): value is string { return typeof value === 'string' && [...value].length > 0 && [...value].length <= 256; }
function owner(value: unknown): boolean { return value === null || fields(value, ['kind', 'id']) && typeof value.kind === 'string' && ['chief', 'manager', 'ticket', 'goal'].includes(value.kind) && text(value.id) && (value.kind !== 'chief' || value.id === 'chief'); }
function fail(message: string): never { throw new Error(message); }
export function windowKey(window: ViewWindowRequest): string { return compact([window.section, window.owner === null ? null : [window.owner.kind, window.owner.id], window.group, window.child_filter]); }
export function viewIdentity(value: ViewRequest): string { return digest({ ...value, scope_generation: 0, windows: [...value.windows].sort((a, b) => binaryCompare(windowKey(a), windowKey(b))) }); }
export function parseView(input: unknown): ViewRequest {
  if (!fields(input, ['projection_version', 'scope_generation', 'locale', 'selected', 'selected_conversation_id', 'review_conversation_ids', 'review_skipped_keys', 'review_priority_keys', 'windows'])) fail('invalid view request fields');
  if (input.projection_version !== 7 || !Number.isSafeInteger(input.scope_generation) || Number(input.scope_generation) < 0) fail('invalid view generation');
  if (typeof input.locale !== 'string' || !VIEW_CONFIG.locales.includes(input.locale)) fail('unsupported navigation ordering locale');
  if (!owner(input.selected) || input.selected_conversation_id !== null && !text(input.selected_conversation_id)) fail('invalid selected owner');
  if (!Array.isArray(input.review_conversation_ids) || input.review_conversation_ids.length > VIEW_CONFIG.maximum_requested_rows || input.review_conversation_ids.some(item => !text(item)) || new Set(input.review_conversation_ids).size !== input.review_conversation_ids.length) fail('invalid Review conversation context');
  for (const name of ['review_skipped_keys', 'review_priority_keys']) {
    const items = input[name];
    if (!Array.isArray(items) || items.some(item => !fields(item, ['kind', 'id']) || typeof item.kind !== 'string' || !['ticket', 'conversation'].includes(item.kind) || !text(item.id))) fail('invalid Review history');
    if (new Set(items.map(item => compact(item))).size !== items.length) fail('duplicate Review history key');
  }
  if (!Array.isArray(input.windows) || input.windows.length > VIEW_CONFIG.maximum_windows) fail('invalid view windows');
  const seen = new Set<string>();
  let total = input.review_conversation_ids.length;
  for (const window of input.windows) {
    if (!fields(window, ['section', 'owner', 'group', 'child_filter', 'anchor', 'direction', 'limit'])) fail('invalid window fields');
    if (typeof window.section !== 'string' || !sections.includes(window.section) || typeof window.group !== 'string' || !groups.includes(window.group) || typeof window.child_filter !== 'string' || !['all', 'active', 'backlogged', 'done'].includes(window.child_filter) || !owner(window.owner)) fail('invalid window membership');
    if (typeof window.direction !== 'string' || !['before', 'after'].includes(window.direction) || !Number.isInteger(window.limit) || Number(window.limit) < 0 || Number(window.limit) > VIEW_CONFIG.maximum_window_rows) fail('invalid window bounds');
    if (window.anchor !== null && (typeof window.anchor !== 'string' || [...window.anchor].length > 8192)) fail('invalid window anchor');
    if (['rail_tickets', 'children'].includes(window.section) && (!object(window.owner) || typeof window.owner.kind !== 'string' || !['chief', 'manager'].includes(window.owner.kind))) fail('window requires a parent');
    if (['rail_parents', 'settled', 'review'].includes(window.section) && window.owner !== null) fail('global window has no parent');
    if ((!['children', 'rail_tickets'].includes(window.section) && window.group !== 'all') || (window.section !== 'children' && window.child_filter !== 'all')) fail('unsupported window filter');
    const identity = windowKey(window as unknown as ViewWindowRequest);
    if (seen.has(identity)) fail('duplicate view window');
    seen.add(identity); total += Number(window.limit);
  }
  if (total > VIEW_CONFIG.maximum_requested_rows) fail('view request exceeds row budget');
  return input as unknown as ViewRequest;
}
