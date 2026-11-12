import type { APNsConfig } from './contracts.ts';
export const APNS_BATCH_SIZE = 50;
export const APNS_DELIVERY_TTL_SECONDS = 300;
export const APNS_MAX_ATTEMPTS = 5;
export const APNS_RETRY_BASE_SECONDS = 30;
export const APNS_TIMEOUT_SECONDS = 10;
export const APNS_JWT_LIFETIME_SECONDS = 3000;
export const APNS_PAYLOAD_MAX_BYTES = 4096;
export const NOTIFICATION_LABEL_MAX_CHARS = 60;
export function load_apns_config(env: NodeJS.ProcessEnv = process.env): APNsConfig | null {
  const key_path = (env.PLAN_APNS_KEY_PATH ?? '').trim();
  const key_id = (env.PLAN_APNS_KEY_ID ?? '').trim();
  const team_id = (env.PLAN_APNS_TEAM_ID ?? '').trim();
  const bundle_ids = (env.PLAN_APNS_BUNDLE_IDS ?? '')
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);
  return key_path && key_id && team_id && bundle_ids.length
    ? { key_path, key_id, team_id, bundle_ids }
    : null;
}
