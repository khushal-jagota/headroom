/** The Review count as a Live Activity: what the iPhone should show, and the push that says so. */
import type { ReadSql } from '../core/database/contracts.ts';
import { review_body } from '../navigation/views/review_cards.ts';
import { reviewMessageItems } from '../navigation/views/review.ts';

/** iOS ends an activity after about 8 hours. The server starts a new one a little before. */
export const LIVE_ACTIVITY_LIFETIME_SECONDS = 7.5 * 3600;
/** A start that the app has not answered with an update token is not repeated before this. */
export const LIVE_ACTIVITY_START_RETRY_SECONDS = 15 * 60;
/** The attributes type that the app and the widget extension share. */
export const LIVE_ACTIVITY_ATTRIBUTES_TYPE = 'ReviewActivityAttributes';

export interface ReviewCardSummary {
  key: string;
  title: string;
}
/** The state that the widget draws. `arrivedTitle` shows only in the expanded Island. */
export interface ReviewActivityContent {
  count: number;
  arrivedTitle?: string;
}
export interface ActivityDeviceState {
  push_to_start_token: string | null;
  update_token: string | null;
  started_at: number | null;
  last_cards: string[] | null;
}
export type LiveActivityPlan =
  | { kind: 'update'; token: string; content: ReviewActivityContent; alert: boolean }
  | { kind: 'start'; token: string; content: ReviewActivityContent };

/**
 * One entry per card, as the phone's summary counts them: a Ticket's proposal, each of its
 * unread runs and its ask, or a Manager's or Chief's run and ask. Oldest first. Null when the
 * queue is unavailable, because an unknown queue must never read as clear.
 */
export function read_review_cards(sql: ReadSql): ReviewCardSummary[] | null {
  const body = review_body(sql);
  if (!body.available) return null;
  const cards: ReviewCardSummary[] = [];
  for (const card of body.cards) {
    const base = `${card.kind}:${card.id}`;
    if (card.waits.includes('proposal')) cards.push({ key: `${base}:proposal`, title: card.title });
    if (card.waits.includes('message')) {
      const runs =
        card.kind === 'ticket'
          ? reviewMessageItems(sql, card.id).map((run) => run.conversation_id)
          : [''];
      for (const run of runs) cards.push({ key: `${base}:message:${run}`, title: card.title });
    }
    if (card.waits.includes('question')) cards.push({ key: `${base}:question`, title: card.title });
  }
  return cards;
}

/** What to push to one device now, or null when the activity already shows the queue. */
export function plan_live_activity(
  device: ActivityDeviceState,
  cards: readonly ReviewCardSummary[],
  now: number,
): LiveActivityPlan | null {
  const content: ReviewActivityContent = { count: cards.length };
  const running =
    device.update_token !== null &&
    device.started_at !== null &&
    now - device.started_at < LIVE_ACTIVITY_LIFETIME_SECONDS;
  if (running) {
    const known = device.last_cards;
    const added = known === null ? [] : cards.filter((card) => !known.includes(card.key));
    const unchanged =
      known !== null &&
      known.length === cards.length &&
      cards.every((card) => known.includes(card.key));
    if (unchanged) return null;
    // The newest card that was not in the last push gives the alert its title.
    const arrived = added.at(-1);
    if (arrived) content.arrivedTitle = arrived.title;
    return { kind: 'update', token: device.update_token!, content, alert: arrived !== undefined };
  }
  if (device.push_to_start_token === null) return null;
  const waiting =
    device.update_token === null &&
    device.started_at !== null &&
    now - device.started_at < LIVE_ACTIVITY_START_RETRY_SECONDS;
  if (waiting) return null;
  return { kind: 'start', token: device.push_to_start_token, content };
}

/** The APNs body. `content-state` must decode into the widget's `ContentState` by key. */
export function live_activity_payload(plan: LiveActivityPlan, now: number): Buffer {
  const alert_body =
    plan.content.arrivedTitle ?? (plan.content.count ? `${plan.content.count} new.` : 'All clear.');
  const aps: Record<string, unknown> = {
    timestamp: now,
    event: plan.kind,
    'content-state': plan.content,
  };
  if (plan.kind === 'start') {
    aps['attributes-type'] = LIVE_ACTIVITY_ATTRIBUTES_TYPE;
    aps.attributes = {};
    aps.alert = { title: 'Review', body: alert_body };
  } else if (plan.alert) {
    aps.alert = { title: 'Review', body: alert_body };
  }
  return Buffer.from(JSON.stringify({ aps }));
}
