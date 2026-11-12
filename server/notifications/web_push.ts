/** The sole boundary to Web Push encryption, VAPID, and transport responses. */
import { createPrivateKey } from 'node:crypto';
import * as webpush from 'web-push';
import type {
  NotificationIntent,
  PushSubscription,
  WebPushAdapter,
  WebPushIdentity,
  WebPushResult,
} from './contracts.ts';
export function notification_payload(intent: NotificationIntent): string {
  return JSON.stringify({
    title: intent.title,
    body: intent.body,
    route: intent.route,
    tag: intent.tag,
  });
}
function vapid_private_key(identity: WebPushIdentity): string {
  const key = createPrivateKey({
    key: Buffer.from(identity.private_key, 'base64url'),
    format: 'der',
    type: 'pkcs8',
  });
  return key.export({ format: 'jwk' }).d!;
}
function options(identity: WebPushIdentity, subject: string): webpush.RequestOptions {
  return {
    TTL: 300,
    timeout: 10000,
    contentEncoding: 'aes128gcm',
    vapidDetails: {
      subject,
      publicKey: identity.public_key,
      privateKey: vapid_private_key(identity),
    },
  };
}
function subscription_info(subscription: PushSubscription): webpush.PushSubscription {
  return {
    endpoint: subscription.endpoint,
    keys: { p256dh: subscription.p256dh, auth: subscription.auth },
  };
}
/** Builds encrypted wire data without opening any network connection. */
export function build_web_push_request(
  subscription: PushSubscription,
  intent: NotificationIntent,
  identity: WebPushIdentity,
  configuration: { subject: string },
): webpush.RequestDetails {
  return webpush.generateRequestDetails(
    subscription_info(subscription),
    notification_payload(intent),
    options(identity, configuration.subject),
  );
}
export class StandardsWebPushAdapter implements WebPushAdapter {
  async send(
    subscription: PushSubscription,
    intent: NotificationIntent,
    identity: WebPushIdentity,
    configuration: { subject: string },
  ): Promise<WebPushResult> {
    try {
      await webpush.sendNotification(
        subscription_info(subscription),
        notification_payload(intent),
        options(identity, configuration.subject),
      );
      return { delivered: true };
    } catch (error) {
      if (error instanceof webpush.WebPushError) {
        const status = error.statusCode;
        return {
          delivered: false,
          expired: [404, 410].includes(status),
          error: `Web Push rejected delivery (${status || 'transport error'})`,
        };
      }
      return {
        delivered: false,
        error: `Web Push failed: ${error instanceof Error ? error.name : 'Error'}`,
      };
    }
  }
}
