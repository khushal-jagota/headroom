/** One push to the owner's devices when an update fails. It has no preference switch. */
import type { ReadAccess } from '../../core/database/contracts.ts';
import type { Config } from '../../core/config.ts';
import type {
  APNsDevice,
  NotificationIntent,
  PushSubscription,
  WebPushIdentity,
} from '../../notifications/contracts.ts';
import { send_push } from '../../stalls/alarms.ts';
import type { UpdateAttempt } from './contracts.ts';

export function update_failure_intent(attempt: UpdateAttempt): NotificationIntent {
  return {
    title: 'Panels: update failed',
    body: attempt.detail ?? 'The update failed. Panels recorded no reason.',
    route: '/#/',
    tag: 'panels-update-failed',
    reply: null,
  };
}

export async function notify_update_failure(
  database: Pick<ReadAccess, 'read'>,
  config: Pick<Config, 'trusted_ingress_canonical_origin' | 'apns'>,
  attempt: UpdateAttempt,
): Promise<void> {
  const targets = database.read((sql) => ({
    web_push_identity:
      sql.get<WebPushIdentity>(
        'SELECT private_key,public_key FROM notification_web_push_identity WHERE singleton=1',
      ) ?? null,
    subscriptions: sql.all<PushSubscription>(
      'SELECT subscription_id,endpoint,p256dh,auth FROM notification_push_subscriptions WHERE disabled_at IS NULL ORDER BY created_at,subscription_id',
    ),
    apns_devices: sql.all<APNsDevice>(
      'SELECT device_id,device_token,bundle_id,environment,registration_version,updated_at FROM notification_apns_devices ORDER BY created_at,device_id',
    ),
  }));
  const { StandardsWebPushAdapter } = await import('../../notifications/web_push.ts');
  const { TokenAPNsAdapter, canonical_server_origin } = await import('../../notifications/apns.ts');
  const origin = canonical_server_origin(config.trusted_ingress_canonical_origin);
  const apns_config = config.apns;
  const apns = apns_config && origin ? new TokenAPNsAdapter(apns_config) : null;
  try {
    await send_push(update_failure_intent(attempt), targets, {
      web_push: new StandardsWebPushAdapter(),
      apns,
      origin,
      now_s: Math.floor(Date.now() / 1000),
      source: {
        label: 'update failure',
        subject_id: 'update',
        notification_type: 'update_failed',
      },
    });
  } finally {
    await apns?.close();
  }
}
