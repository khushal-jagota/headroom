import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import type { TranscriptRow } from './transcript';
import type { ConversationViewItem } from './view/contracts';
import { messageContentText } from './wire';

/** Only the newest addressed reply supplies the preview, including attachment-only replies. */
export function latestUnreadReply(rows: readonly TranscriptRow[], readThrough: number): string | null {
  for (let index = rows.length - 1; index >= 0; index--) {
    const row = rows[index];
    if (row.kind === 'agent_message' && row.toOwner) {
      return row.sequence > readThrough ? messageContentText(row.content) : null;
    }
  }
  return null;
}

const parser = unified().use(remarkParse).use(remarkGfm);
/** Plain text keeps block boundaries without turning a closed dock into another renderer. */
export function dockPreviewText(source: string | null | undefined): string | null {
  if (!source?.trim()) return null;
  const tree = parser.parse(source);
  function text(node: { type: string; value?: string; alt?: string | null; children?: unknown[] }): string {
    if (node.type === 'html' || node.type === 'definition' || node.type === 'thematicBreak') return '';
    if (node.type === 'image') return node.alt ?? '';
    if (node.type === 'break') return '\n';
    if (node.value !== undefined) return node.value;
    const separator = ['root', 'blockquote', 'list', 'listItem', 'table', 'tableRow'].includes(node.type) ? '\n' : '';
    return (node.children ?? []).map(child => text(child as Parameters<typeof text>[0])).join(separator);
  }
  return text(tree).replace(/\n[ \t]*\n+/g, '\n').trim() || null;
}

const FEEDBACK_KINDS = ['prompt_refused', 'prompt_uncertain', 'prompt_discarded', 'turn_stopped', 'turn_ended'];
/** Exceptional dock feedback for the latest thing that happened. Its words are that event's
 * own, never the running turn's line: an earlier ending beside a new running turn must not
 * repeat what the activity line already says. */
export function dockFeedbackFor(latest: { kind: string; ending?: string } | null, said: { text: string } | null): string | null {
  if (!latest || !FEEDBACK_KINDS.includes(latest.kind)) return null;
  if (latest.kind === 'turn_ended' && latest.ending === 'completed') return null;
  return said?.text || null;
}

type PaneRole = 'Worker' | 'Manager' | 'Chief' | 'Goal';
const ROLE_TITLES: Record<PaneRole, string> = { Worker: 'Worker', Manager: 'Manager', Chief: 'Chief of Staff', Goal: 'Goal' };
/** Who the opened conversation is with, for its top bar: the owner's own name for it when the
 * page gave one, else the role the pane already knows. Never blank, never invented. */
export function paneTitleFor(title: string | null | undefined, role: PaneRole): string {
  return title?.trim() || ROLE_TITLES[role];
}

/** Whether a key request in this conversation still waits on the person. Unlike a permission
 * ask or a question it is a row, not a status fact, so it is read from the rows the pane
 * holds: the record's rows, or the view's held items, where the newest version of a row
 * speaks for it. Blocking on the person is the same state whichever card asks. */
export function awaitsCredential(
  rows: readonly TranscriptRow[],
  items: readonly ConversationViewItem[] = []
): boolean {
  if (rows.some(row => row.kind === 'credential_request' && row.request.state === 'pending')) return true;
  const newest = new Map<string, ConversationViewItem & { kind: 'row' }>();
  for (const item of items) {
    if (item.kind !== 'row' || item.row_kind !== 'credential_request' || item.body.kind !== 'inline') continue;
    const held = newest.get(item.key);
    if (!held || held.changed_through_sequence <= item.changed_through_sequence) newest.set(item.key, item);
  }
  for (const item of newest.values()) {
    const value = item.body.kind === 'inline' ? item.body.value : null;
    if (value?.kind === 'credential_request' && value.state === 'pending') return true;
  }
  return false;
}

/** What went wrong last, if the last thing that happened went wrong: the newest turn ended
 * failed, or the newest message was refused delivery. Read from the same facts the dock's
 * line and the transcript's error rows are drawn from, so the bar never says more than they
 * do. A message sent after a failure is the person moving on, and the failure goes with it. */
export type PaneFailure = 'turn_failed' | 'not_delivered';
export function paneFailureFrom(facts: {
  /** The newest turn boundary (a turn ending or a message starting one) is a failed ending. */
  lastBoundaryFailed: boolean;
  /** The kind of the newest thing that happened, as the dock reads it. */
  latestKind: string | null;
}): PaneFailure | null {
  if (facts.latestKind === 'prompt_refused') return 'not_delivered';
  if (facts.lastBoundaryFailed) return 'turn_failed';
  return null;
}
const FAILURE_TEXT: Record<PaneFailure, string> = { turn_failed: 'Last turn failed', not_delivered: 'Message not delivered' };

/** What the opened conversation is doing, as its top bar says it. `live` draws the moving
 * mark, `attention` the person's own accent, `failed` the error mark, `quiet` words alone.
 * Idle says nothing: the transcript already shows a conversation at rest. */
export type PaneHeadStatus = { text: string; tone: 'live' | 'attention' | 'failed' | 'quiet' };
export function paneHeadStatus(state: {
  readOnly: boolean;
  activityUncertain: boolean;
  waitingForYou: boolean;
  running: boolean;
  backgroundWorking: boolean;
  /** The Worker-setup sentence ("Setting up · preparing workspace · 1m 35s"), or null. */
  settingUp: string | null;
  /** Seconds the running turn has taken, or null while that is not known. */
  elapsed: string | null;
  /** What went wrong last, or null. Anything still moving says that instead. */
  failure?: PaneFailure | null;
}): PaneHeadStatus | null {
  if (state.readOnly) return { text: 'Past conversation · read only', tone: 'quiet' };
  if (state.activityUncertain) return { text: 'Activity on this machine unknown', tone: 'quiet' };
  if (state.waitingForYou) return { text: 'Waiting for you', tone: 'attention' };
  if (state.running) return { text: state.elapsed ? `Working · ${state.elapsed}` : 'Working', tone: 'live' };
  if (state.backgroundWorking) return { text: 'Working in the background', tone: 'live' };
  if (state.failure) return { text: FAILURE_TEXT[state.failure], tone: 'failed' };
  if (state.settingUp) return { text: state.settingUp, tone: 'live' };
  return null;
}

/** The dock's line under the status while the machine's own activity cannot be read. Setup
 * is the one state that line would otherwise contradict ("Setting up" beside "unknown"), so
 * it says the two together: the setup is recorded, the machine cannot confirm it now. */
export function uncertainActivityFeedback(settingUp: boolean): string {
  return settingUp
    ? 'This machine is not connected, so setup progress cannot be confirmed.'
    : 'Current activity on this machine is unknown.';
}
