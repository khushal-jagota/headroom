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

/** Whether the newest thing that happened is the turn being stopped: a turn that ended
 * interrupted, or one the record says stopped. It holds until the next turn starts. */
export function interruptedFrom(latest: { kind: string; ending?: string } | null | undefined): boolean {
  if (!latest) return false;
  return latest.kind === 'turn_stopped' || (latest.kind === 'turn_ended' && latest.ending === 'interrupted');
}

/** The one state the conversation is in, which the opened pane's header, the dock's status
 * line and the turn's meta line all read, so they can never disagree. Its words never name a
 * tool or a command: Focus does not speak of the work, and these are read in both lenses. */
export type ConversationActivity =
  | 'idle' | 'working' | 'waiting' | 'failed' | 'undelivered' | 'interrupted' | 'setting-up'
  | 'unknown' | 'read-only';
export type ConversationStateReading = {
  state: ConversationActivity;
  /** The words the header and the dock say. Empty for idle. */
  text: string;
  /** Which mark is drawn: spinner, accent dot, alert, stop square, or nothing. */
  mark: 'spinner' | 'dot' | 'alert' | 'stop' | null;
};
export type ConversationStateFacts = {
  readOnly: boolean;
  activityUncertain: boolean;
  waitingForYou: boolean;
  running: boolean;
  backgroundWorking: boolean;
  /** The Worker-setup sentence ("Setting up · preparing workspace · 1m 35s"), or null. */
  settingUp: string | null;
  /** How long the running turn has taken, formatted, or null while that is not known. */
  elapsed: string | null;
  /** What went wrong last, or null. */
  failure?: PaneFailure | null;
  /** The newest thing that happened is the turn being stopped. */
  interrupted?: boolean;
};

/** Precedence: waiting > failed / not delivered > working > setting up > interrupted > idle.
 * A machine Panels cannot read, and a past conversation, say only that. */
export function conversationState(facts: ConversationStateFacts): ConversationStateReading {
  if (facts.readOnly) return { state: 'read-only', text: 'Past conversation · read only', mark: null };
  // Setup the machine cannot confirm is still setup: setting up is recorded, and normal.
  if (facts.activityUncertain && facts.settingUp && !facts.waitingForYou) return { state: 'setting-up', text: facts.settingUp, mark: 'spinner' };
  if (facts.activityUncertain) return { state: 'unknown', text: 'Activity on this machine unknown', mark: null };
  if (facts.waitingForYou) return { state: 'waiting', text: 'Waiting for you', mark: 'dot' };
  if (facts.failure === 'turn_failed') return { state: 'failed', text: 'Turn failed', mark: 'alert' };
  if (facts.failure === 'not_delivered') return { state: 'undelivered', text: 'Message not delivered', mark: 'alert' };
  if (facts.running) return { state: 'working', text: facts.elapsed ? `Working · ${facts.elapsed}` : 'Working', mark: 'spinner' };
  if (facts.backgroundWorking) return { state: 'working', text: 'Working in the background', mark: 'spinner' };
  if (facts.settingUp) return { state: 'setting-up', text: facts.settingUp, mark: 'spinner' };
  if (facts.interrupted) return { state: 'interrupted', text: 'Interrupted', mark: 'stop' };
  return { state: 'idle', text: '', mark: null };
}

/** What the opened conversation is doing, as its top bar says it, or null when idle (the
 * transcript already shows a conversation at rest). */
export type PaneHeadStatus = ConversationStateReading;
export function paneHeadStatus(facts: ConversationStateFacts): PaneHeadStatus | null {
  const reading = conversationState(facts);
  return reading.state === 'idle' ? null : reading;
}

/** The dock's line under the status while the machine's own activity cannot be read. Setup
 * is the one state that line would otherwise contradict ("Setting up" beside "unknown"), so
 * it says the two together: the setup is recorded, the machine cannot confirm it now. */
export function uncertainActivityFeedback(settingUp: boolean): string {
  return settingUp
    ? 'This machine is not connected, so setup progress cannot be confirmed.'
    : 'Current activity on this machine is unknown.';
}

/** Copies a fact (the workspace path) for the person. Says whether it landed, so the control
 * only says "Copied" when it did. */
export async function copyText(
  text: string,
  clipboard: Pick<Clipboard, 'writeText'> | undefined = globalThis.navigator?.clipboard
): Promise<boolean> {
  if (!clipboard) return false;
  try { await clipboard.writeText(text); return true; } catch { return false; }
}
