import type { FastifyPluginAsync, FastifyRequest } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwner } from '../core/authority/identity.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import {
  NOTIFICATION_SUBJECTS,
  NOTIFICATION_TYPE_BY_ID,
  notification_preference_is_valid,
} from './contracts.ts';
import type { APNsDevice } from './contracts.ts';
import { canonical_server_origin } from './apns.ts';
import * as data from './data.ts';
import * as apns_data from './apns_data.ts';
function settings(sql: ReadSql) {
  const preferences = data.resolved_preferences(sql);
  const identity = data.read_web_push_identity(sql);
  return {
    subjects: NOTIFICATION_SUBJECTS.map((subject) => ({
      key: subject.key,
      label: subject.label,
      types: subject.notification_type_ids.map((id) => {
        const type = NOTIFICATION_TYPE_BY_ID.get(id)!;
        return {
          id,
          label: type.label,
          description: type.description,
          enabled: preferences.get(data.preference_key(subject.key, id))!,
        };
      }),
    })),
    vapid_public_key: identity.public_key,
  };
}
function authenticated_owner(request: FastifyRequest, services: AreaServices): void {
  requireOwner(services.identity(request));
  if ((request as FastifyRequest & { authenticatedOwner?: boolean }).authenticatedOwner !== true)
    throw new PlannerError('agent_forbidden', 'authenticated owner access is required');
}
const deviceBody = {
  type: 'object',
  required: ['device_token', 'bundle_id', 'environment'],
  properties: {
    device_token: { type: 'string', minLength: 2, maxLength: 512, pattern: '^(?:[0-9a-f]{2})+$' },
    bundle_id: { type: 'string', minLength: 1, maxLength: 255, pattern: '^[A-Za-z0-9.-]+$' },
    environment: { type: 'string', enum: ['development', 'production'] },
  },
};
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.get('/notifications/settings', async () => services.database.read(settings));
  app.put<{
    Params: { subject_key: string; notification_type: string };
    Body: { enabled: boolean };
  }>(
    '/notifications/preferences/:subject_key/:notification_type',
    {
      schema: {
        body: {
          type: 'object',
          required: ['enabled'],
          properties: { enabled: { type: 'boolean' } },
        },
      },
    },
    async (request) => {
      const { subject_key, notification_type } = request.params;
      if (!notification_preference_is_valid(subject_key, notification_type))
        throw new PlannerError(
          'not_found',
          `notification preference not found: ${subject_key}/${notification_type}`,
        );
      return services.database.write((sql) => {
        data.set_preference(
          sql,
          subject_key,
          notification_type,
          request.body.enabled,
          services.clock.nowUnix(),
        );
        return settings(sql);
      });
    },
  );
  app.post<{ Body: { endpoint: string; keys: { p256dh: string; auth: string } } }>(
    '/notifications/subscriptions',
    {
      schema: {
        body: {
          type: 'object',
          required: ['endpoint', 'keys'],
          properties: {
            endpoint: { type: 'string', minLength: 12, maxLength: 4096 },
            keys: {
              type: 'object',
              required: ['p256dh', 'auth'],
              properties: {
                p256dh: { type: 'string', minLength: 8, maxLength: 512 },
                auth: { type: 'string', minLength: 8, maxLength: 256 },
              },
            },
          },
        },
      },
    },
    async (request) => {
      const body = request.body;
      let valid = false;
      try {
        const authority = /^https:\/\/([^/?#]+)/i.exec(body.endpoint)?.[1];
        const url = new URL(body.endpoint);
        valid =
          authority !== undefined &&
          !authority.includes('@') &&
          url.protocol === 'https:' &&
          !!url.host;
      } catch {
        /* invalid endpoint reports the product error below */
      }
      if (!valid)
        throw new PlannerError('validation', 'push subscription endpoint must be an HTTPS URL');
      return services.database.write((sql) => ({
        subscription_id: data.register_subscription(sql, {
          endpoint: body.endpoint,
          p256dh: body.keys.p256dh,
          auth: body.keys.auth,
          now: services.clock.nowUnix(),
        }).subscription_id,
      }));
    },
  );
  app.delete<{ Params: { subscription_id: string } }>(
    '/notifications/subscriptions/:subscription_id',
    async (request) =>
      services.database.write((sql) => ({
        removed: data.remove_subscription(sql, request.params.subscription_id),
      })),
  );
  app.put<{
    Body: { device_token: string; bundle_id: string; environment: APNsDevice['environment'] };
  }>('/notifications/apns/devices', { schema: { body: deviceBody } }, async (request) => {
    authenticated_owner(request, services);
    const config = services.config.apns ?? null;
    if (!config || !canonical_server_origin(services.config.trusted_ingress_canonical_origin))
      throw new PlannerError('gateway_offline', 'native push delivery is not configured');
    if (!config.bundle_ids.includes(request.body.bundle_id))
      throw new PlannerError('validation', 'native push bundle ID is not allowed');
    services.database.write((sql) =>
      apns_data.register_device(sql, { ...request.body, now: services.clock.nowUnix() }),
    );
    return { registered: true };
  });
  app.delete<{
    Body: { device_token: string; bundle_id: string; environment: APNsDevice['environment'] };
  }>('/notifications/apns/devices', { schema: { body: deviceBody } }, async (request) => {
    authenticated_owner(request, services);
    return services.database.write((sql) => ({
      removed: apns_data.remove_device(sql, request.body),
    }));
  });
};
