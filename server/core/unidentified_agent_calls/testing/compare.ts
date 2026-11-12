/** Export-only companion for the single synthetic source-comparison sequence. */
import Fastify from 'fastify';
import { join } from 'node:path';
import { loadConfig } from '../../config.ts';
import { installMiddleware } from '../../http/middleware.ts';
import { admit_unidentified_call, http_refusal, websocket_refusal } from '../admission.ts';
import type { Endpoint, UnidentifiedCallRequest } from '../contracts.ts';
import { proc_reader, resolve_loopback_peer } from '../peer.ts';

interface Probe {
  config: string;
  little_endian: boolean;
  uid: string;
  peers: { root: string; client: Endpoint; fault?: string }[];
  requests: (UnidentifiedCallRequest & { enabled: boolean })[];
}
export async function refusal_probe(spec: Probe): Promise<unknown> {
  const peers = [];
  for (const peer of spec.peers) {
    let reads = 0;
    const denied = (): never => {
      throw Object.assign(new Error('synthetic permission'), { code: 'EACCES' });
    };
    peers.push(
      await resolve_loopback_peer(peer.client, ['127.0.0.1', 8767], {
        proc_root: peer.root,
        current_uid: BigInt(spec.uid),
        little_endian: spec.little_endian,
        files: {
          ...proc_reader,
          async list(path) {
            if (peer.fault?.startsWith('deny') && path === join(peer.root, '202/fd')) denied();
            return proc_reader.list(path);
          },
          async readText(path) {
            if (peer.fault === 'deny_unknown' && path === join(peer.root, '202/cgroup')) denied();
            return proc_reader.readText(path);
          },
          async readlink(path) {
            if (peer.fault === 'race' && path === join(peer.root, '101/fd/4') && ++reads === 2)
              return 'socket:[99999]';
            return proc_reader.readlink(path);
          },
        },
      }),
    );
  }
  const requests = [];
  for (const request of spec.requests) {
    let lookups = 0;
    const admitted = await admit_unidentified_call(request.enabled, request, async () => {
      lookups++;
      return { is_owner: false, skipped_unreadable_processes: 2 };
    });
    requests.push({
      lookups,
      response: admitted.admitted
        ? 'forwarded'
        : request.kind === 'websocket'
          ? websocket_refusal()
          : http_refusal(2),
    });
  }
  const values = [undefined, 'yes', ' OFF ', '\u001cyes\u001f', 'invalid'];
  const configs = values.map((value) => {
    const env = value === undefined ? {} : { PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS: value };
    try {
      return {
        value: loadConfig(spec.config, env).refuse_unidentified_agent_calls,
        unchanged: env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS === value,
      };
    } catch (error) {
      return {
        error: (error as Error).message,
        unchanged: env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS === value,
      };
    }
  });
  const saved = process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS;
  let consumed: boolean, invalid_retained: boolean;
  try {
    process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS = 'yes';
    consumed =
      loadConfig(spec.config).refuse_unidentified_agent_calls &&
      process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS === undefined;
    process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS = 'invalid';
    try {
      loadConfig(spec.config);
    } catch {
      /* Parsing must leave the invalid value. */
    }
    invalid_retained = process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS === 'invalid';
  } finally {
    if (saved === undefined) delete process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS;
    else process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS = saved;
  }
  const app = Fastify();
  let lookups = 0;
  installMiddleware(
    app,
    {
      ...loadConfig(spec.config, {}),
      refuse_unidentified_agent_calls: true,
      trusted_ingress_provider: 'tailscale',
      trusted_ingress_allowed_login: 'owner@example.test',
      trusted_ingress_canonical_origin: 'https://panels.example.test',
    },
    undefined,
    async () => {
      lookups++;
      return { is_owner: false, skipped_unreadable_processes: 2 };
    },
  );
  app.get('/api/meta', async () => ({ forwarded: true }));
  let http;
  try {
    const refused = await app.inject({ method: 'GET', url: '/api/meta' });
    const owner = await app.inject({
      method: 'GET',
      url: '/api/meta',
      headers: { 'tailscale-user-login': 'owner@example.test' },
    });
    http = {
      refused: {
        status: refused.statusCode,
        body: refused.json(),
        type: refused.headers['content-type'],
      },
      owner: { status: owner.statusCode, body: owner.json() },
      lookups,
    };
  } finally {
    await app.close();
  }
  return { peers, requests, configs, consumed, invalid_retained, http };
}
