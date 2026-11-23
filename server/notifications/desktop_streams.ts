/**
 * The desktop apps that hold a live notification stream to this server.
 *
 * The delivery pass decides each notification once, through the same policy as Web Push
 * and APNs. After that decision commits, it hands the notification to every open desktop
 * stream. Nothing is stored for the desktop: an app that is not connected misses it.
 */
import type { Database } from '../core/database/contracts.ts';
import type { NotificationIntent } from './contracts.ts';

/** What a desktop app shows. A newer notification with the same tag replaces the older one. */
export type DesktopNotification = Pick<NotificationIntent, 'title' | 'body' | 'route' | 'tag'>;

export class DesktopNotificationStreams {
  #listeners = new Set<(notification: DesktopNotification) => void>();

  get listening(): boolean {
    return this.#listeners.size > 0;
  }

  listen(listener: (notification: DesktopNotification) => void): () => void {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  }

  send(intent: NotificationIntent): void {
    const notification: DesktopNotification = {
      title: intent.title,
      body: intent.body,
      route: intent.route,
      tag: intent.tag,
    };
    for (const listener of [...this.#listeners]) listener(notification);
  }
}

// One set of streams for each server, found through the database both sides already hold:
// the delivery pass in the background and the stream route in HTTP.
const STREAMS = new WeakMap<Database, DesktopNotificationStreams>();

export function desktop_notification_streams(database: Database): DesktopNotificationStreams {
  let streams = STREAMS.get(database);
  if (!streams) STREAMS.set(database, (streams = new DesktopNotificationStreams()));
  return streams;
}
