import type { ConversationViewItem, ConversationViewStatus, ConversationViewTurn } from './contracts';

/** v6: the running turn, if one is held: the unsettled turn the status names. Its live
 *  status line stands at the end of the transcript, not at the turn's top. */
export function liveTurnOf(pages: readonly { items: readonly ConversationViewItem[] }[], status: ConversationViewStatus | null | undefined): ConversationViewTurn | null {
  const key = status?.turn_key ?? null;
  if (key === null || status?.stopped_without_ending) return null;
  for (const page of pages)
    for (const item of page.items)
      if (item.kind === 'turn' && item.key === key && !item.settled) return item;
  return null;
}

/** Whether a turn's head is drawn at its top: a live turn's is not (it is the live edge). */
export function turnHeadAtTop(turn: ConversationViewTurn, status: ConversationViewStatus | null | undefined): boolean {
  return turn.settled || status?.turn_key !== turn.key || Boolean(status?.stopped_without_ending);
}

/** v8 (4): whether the live turn has a tool call running. In Full that row carries the
 *  spinner, and the Working line shows a still dot, so only one spinner is on screen. */
export function liveTurnRunsATool(pages: readonly { items: readonly ConversationViewItem[] }[], turnKey: string): boolean {
  for (const page of pages)
    for (const item of page.items)
      if (item.kind === 'row' && item.row_kind === 'tool_call' && item.turn_key === turnKey
        && item.body.kind === 'inline' && item.body.value.kind === 'tool_call' && item.body.value.status === 'running') return true;
  return false;
}
