/** The sole boundary to APNs HTTP/2, JWTs, and response reasons. */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { connect } from 'node:http2';
import type { ClientHttp2Session } from 'node:http2';
import { importPKCS8, SignJWT } from 'jose';
import type {
  APNsAdapter,
  APNsConfig,
  APNsDevice,
  APNsResult,
  LiveActivityPush,
  NotificationIntent,
  PendingAPNsDelivery,
} from './contracts.ts';
import {
  APNS_DELIVERY_TTL_SECONDS,
  LIVE_ACTIVITY_EXPIRATION_SECONDS,
  MESSAGE_REPLY_CATEGORY,
  APNS_JWT_LIFETIME_SECONDS,
  APNS_PAYLOAD_MAX_BYTES,
  APNS_TIMEOUT_SECONDS,
} from './config.ts';
/** One payload assembly point for the native text-reply contract. */
export function notification_payload(intent: NotificationIntent, origin: string): Buffer {
  return Buffer.from(
    JSON.stringify({
      aps: {
        alert: { title: intent.title, body: intent.body },
        sound: 'default',
        'thread-id': intent.tag,
        ...(intent.reply === null ? {} : { category: MESSAGE_REPLY_CATEGORY }),
      },
      server_url: origin,
      route: intent.route,
      ...(intent.reply === null ? {} : { reply: intent.reply }),
    }),
  );
}
export function canonical_server_origin(raw: string | null | undefined): string | null {
  if (!raw) return null;
  // Preserve the spelling of the authority, including an explicit default port.
  // URL.origin normalizes it, while Python returns the parsed netloc unchanged.
  const match = /^https:\/\/([^/?#]+)(\/?)(?:\?([^#]*))?(?:#(.*))?$/i.exec(raw);
  if (!match || match[1].includes('@') || match[3] || match[4]) return null;
  try {
    const parts = new URL(raw);
    if (!parts.hostname) return null;
    return `https://${match[1]}`;
  } catch {
    return null;
  }
}
export class TokenAPNsAdapter implements APNsAdapter {
  private jwt: string | null = null;
  private jwt_issued_at = 0;
  private readonly sessions = new Map<string, ClientHttp2Session>();
  readonly config: APNsConfig;
  constructor(config: APNsConfig) {
    this.config = config;
  }
  close(): void {
    for (const session of this.sessions.values()) session.close();
    this.sessions.clear();
  }
  private async provider_token(now: number): Promise<string> {
    if (
      this.jwt === null ||
      now - this.jwt_issued_at < 0 ||
      now - this.jwt_issued_at >= APNS_JWT_LIFETIME_SECONDS
    ) {
      const key = await importPKCS8(readFileSync(this.config.key_path, 'utf8'), 'ES256');
      this.jwt = await new SignJWT({ iss: this.config.team_id, iat: now })
        .setProtectedHeader({ alg: 'ES256', kid: this.config.key_id, typ: 'JWT' })
        .sign(key);
      this.jwt_issued_at = now;
    }
    return this.jwt;
  }
  async send(
    delivery: PendingAPNsDelivery,
    options: { server_url: string; now: number },
  ): Promise<APNsResult> {
    const device = delivery.device;
    const origin = canonical_server_origin(options.server_url);
    if (!origin || !this.config.bundle_ids.includes(device.bundle_id))
      return { delivered: false, error: 'Native delivery configuration does not match' };
    const intent = delivery.intent;
    const payload = notification_payload(intent, origin);
    if (payload.length > APNS_PAYLOAD_MAX_BYTES)
      return { delivered: false, error: 'Native notification payload exceeds the size limit' };
    return this.deliver(
      {
        environment: device.environment,
        token: device.device_token,
        topic: device.bundle_id,
        push_type: 'alert',
        priority: '10',
        expiration: delivery.created_at + APNS_DELIVERY_TTL_SECONDS,
        collapse_id: createHash('sha256').update(intent.tag).digest('hex'),
      },
      payload,
      options.now,
    );
  }
  /** A Live Activity push goes to the app's own topic with the live activity push type. */
  async send_live_activity(push: LiveActivityPush, options: { now: number }): Promise<APNsResult> {
    if (!this.config.bundle_ids.includes(push.bundle_id))
      return { delivered: false, error: 'Native delivery configuration does not match' };
    if (push.payload.length > APNS_PAYLOAD_MAX_BYTES)
      return { delivered: false, error: 'Live Activity payload exceeds the size limit' };
    return this.deliver(
      {
        environment: push.environment,
        token: push.token,
        topic: `${push.bundle_id}.push-type.liveactivity`,
        push_type: 'liveactivity',
        priority: '10',
        expiration: options.now + LIVE_ACTIVITY_EXPIRATION_SECONDS,
      },
      push.payload,
      options.now,
    );
  }
  private async deliver(
    request_options: {
      environment: APNsDevice['environment'];
      token: string;
      topic: string;
      push_type: 'alert' | 'liveactivity';
      priority: string;
      expiration: number;
      collapse_id?: string;
    },
    payload: Buffer,
    now: number,
  ): Promise<APNsResult> {
    const host =
      request_options.environment === 'development'
        ? 'api.sandbox.push.apple.com'
        : 'api.push.apple.com';
    let response: { status: number; body: string };
    try {
      const token = await this.provider_token(now);
      let session = this.sessions.get(host);
      if (!session || session.destroyed || session.closed) {
        const opened = connect(`https://${host}`);
        session = opened;
        opened.on('error', (error) => {
          console.warn('APNs session failed', host, error);
          if (this.sessions.get(host) === opened) this.sessions.delete(host);
        });
        this.sessions.set(host, session);
      }
      const current = session;
      response = await new Promise((resolve, reject) => {
        const request = current.request({
          ':method': 'POST',
          ':path': `/3/device/${request_options.token}`,
          authorization: `bearer ${token}`,
          'content-type': 'application/json',
          'apns-topic': request_options.topic,
          'apns-push-type': request_options.push_type,
          'apns-priority': request_options.priority,
          'apns-expiration': String(request_options.expiration),
          ...(request_options.collapse_id
            ? { 'apns-collapse-id': request_options.collapse_id }
            : {}),
        });
        let status = 0;
        const chunks: Buffer[] = [];
        request.on('response', (headers) => {
          status = Number(headers[':status']);
        });
        request.on('data', (chunk: Buffer) => chunks.push(chunk));
        request.on('error', reject);
        request.setTimeout(APNS_TIMEOUT_SECONDS * 1000, () =>
          request.destroy(new Error('APNs request timeout')),
        );
        request.on('end', () => resolve({ status, body: Buffer.concat(chunks).toString('utf8') }));
        request.end(payload);
      });
    } catch (error) {
      console.warn('APNs delivery failed', host, error);
      return {
        delivered: false,
        retryable: true,
        error: 'Native push transport or signing failed',
      };
    }
    if (response.status === 200) return { delivered: true };
    let detail: Record<string, unknown> = {};
    try {
      const value: unknown = JSON.parse(response.body);
      if (value !== null && typeof value === 'object' && !Array.isArray(value))
        detail = value as Record<string, unknown>;
    } catch {
      /* malformed provider detail carries no reason */
    }
    const reason = typeof detail.reason === 'string' ? detail.reason : null;
    const dead =
      (response.status === 410 && ['Unregistered', 'ExpiredToken'].includes(reason ?? '')) ||
      (response.status === 400 &&
        ['BadDeviceToken', 'DeviceTokenNotForTopic'].includes(reason ?? ''));
    return {
      delivered: false,
      dead_token: dead,
      retryable: [429, 500, 503].includes(response.status),
      error: `Native push rejected delivery (HTTP ${response.status})`,
      invalidated_at_ms:
        response.status === 410 &&
        typeof detail.timestamp === 'number' &&
        Number.isInteger(detail.timestamp)
          ? detail.timestamp
          : null,
    };
  }
}
