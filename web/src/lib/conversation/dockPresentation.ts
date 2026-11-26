import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import type { TranscriptRow } from './transcript';
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

type PaneRole = 'Worker' | 'Manager' | 'Chief' | 'Goal';
const ROLE_TITLES: Record<PaneRole, string> = { Worker: 'Worker', Manager: 'Manager', Chief: 'Chief of Staff', Goal: 'Goal' };
/** Who the opened conversation is with, for its top bar: the owner's own name for it when the
 * page gave one, else the role the pane already knows. Never blank, never invented. */
export function paneTitleFor(title: string | null | undefined, role: PaneRole): string {
  return title?.trim() || ROLE_TITLES[role];
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
  | 'offline' | 'read-only';
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
  /** What the person is asked for, in the ask's own words ("Needs your approval"). One
   *  wording per state: the ask card, the dock's line and the sidebar all say it this way. */
  waitingFor?: 'approval' | 'answer' | 'key' | null;
  running: boolean;
  backgroundWorking: boolean;
  /** "Setting up" while the Worker is prepared, or null. No step and no clock. */
  settingUp: string | null;
  /** How long the running turn has taken, formatted, or null while that is not known. */
  elapsed: string | null;
  /** What went wrong last, or null. */
  failure?: PaneFailure | null;
  /** The newest thing that happened is the turn being stopped. */
  interrupted?: boolean;
};

/** Precedence: waiting > failed / not delivered > working > setting up > interrupted > idle.
 * A machine Panels cannot read, and a past conversation, say only that: "Machine offline",
 * the dock's words, with a still dot and no clock, also while its setup is recorded. */
export function conversationState(facts: ConversationStateFacts): ConversationStateReading {
  if (facts.readOnly) return { state: 'read-only', text: 'Past conversation · read only', mark: null };
  if (facts.activityUncertain) return { state: 'offline', text: 'Machine offline', mark: 'dot' };
  if (facts.waitingForYou) return { state: 'waiting', text: waitingWords(facts.waitingFor ?? null), mark: 'dot' };
  if (facts.failure === 'turn_failed') return { state: 'failed', text: 'Turn failed', mark: 'alert' };
  if (facts.failure === 'not_delivered') return { state: 'undelivered', text: 'Message not delivered', mark: 'alert' };
  if (facts.running) return { state: 'working', text: facts.elapsed ? `Working for ${facts.elapsed}` : 'Working', mark: 'spinner' };
  if (facts.backgroundWorking) return { state: 'working', text: 'Working in the background', mark: 'spinner' };
  if (facts.settingUp) return { state: 'setting-up', text: facts.settingUp, mark: 'spinner' };
  if (facts.interrupted) return { state: 'interrupted', text: 'Interrupted', mark: 'stop' };
  return { state: 'idle', text: '', mark: null };
}

/** The words for a conversation waiting on the person, the same words its ask card leads with. */
export function waitingWords(kind: 'approval' | 'answer' | 'key' | null): string {
  return kind === 'approval' ? 'Needs your approval'
    : kind === 'answer' ? 'Needs your answer'
    : kind === 'key' ? 'Needs a key'
    : 'Needs you';
}

/** The states the opened pane's top bar says. Each state is said once, in one place: working
 * by the running turn's own line, waiting by the ask docked over the composer, a failure by its
 * callout or note in the thread, an interruption by its callout. The top bar says only what the
 * thread has no place for: a machine still being set up, or one Panels cannot read, and a past
 * conversation. */
const HEAD_STATES: ReadonlySet<ConversationActivity> = new Set(['setting-up', 'offline', 'read-only']);
export type PaneHeadStatus = ConversationStateReading;
export function paneHeadStatus(facts: ConversationStateFacts): PaneHeadStatus | null {
  const reading = conversationState(facts);
  return HEAD_STATES.has(reading.state) ? reading : null;
}

/** The dock's status line says one of four things, whatever else waits: the ask, the proposal
 * and a failure each say themselves on the page, the ask card or the transcript. A running turn
 * says Working while an ask waits too, as the server counts it working until the turn ends. A
 * machine Panels cannot read says only that, with no motion and no clock. */
export type DockStatus =
  | { kind: 'working'; clock: string | null }
  | { kind: 'setting-up' | 'idle' | 'offline'; clock: null };
export function dockStatusFrom(facts: {
  conversation: ConversationStateFacts;
  /** A question, permission ask or key request waits in the conversation. */
  askWaiting: boolean;
  offline: boolean;
  turnClock: string | null;
}): DockStatus {
  if (facts.offline) return { kind: 'offline', clock: null };
  const state = conversationState({ ...facts.conversation, waitingForYou: facts.askWaiting && !facts.conversation.running }).state;
  if (state === 'setting-up') return { kind: 'setting-up', clock: null };
  if (state === 'working') return { kind: 'working', clock: facts.turnClock };
  return { kind: 'idle', clock: null };
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
