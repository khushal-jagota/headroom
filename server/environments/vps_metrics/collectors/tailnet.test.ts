import { expect, it } from 'vitest';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { metrics_config } from '../configuration.ts';
import type { Measurement } from '../contracts.ts';
import { launcher } from './command.ts';
import { query_tailscale } from './tailscale.ts';
import { TailnetReader } from './tailnet.ts';

const config = metrics_config({ tailnet_query_seconds: 0.2 });
const peer = (CurAddr: string, extra: object = {}) => ({
  DNSName: 'khushals-macbook-pro.tail687c57.ts.net.',
  Online: true,
  Active: true,
  CurAddr,
  Relay: 'par',
  LastHandshake: '2026-11-27T03:08:04.224286885+01:00',
  ...extra,
});
const status = (...peers: object[]) =>
  JSON.stringify({ Self: { Relay: 'fra' }, Peer: Object.fromEntries(peers.map((p, i) => [i, p])) });
const counters = (callmemaybe: number, relay_home: number, send: number, recv: number) =>
  [
    '# TYPE magicsock_disco_sent_callmemaybe counter',
    `magicsock_disco_sent_callmemaybe ${callmemaybe}`,
    `derp_home_change ${relay_home}`,
    `magicsock_send_data_derp ${send}`,
    `magicsock_recv_data_derp ${recv}`,
  ].join('\n');
const scripted = (...outputs: [string, string][]) => {
  let call = -1;
  return async (args: readonly string[]) => {
    if (args[0] === 'status') call++;
    return outputs[call]![args[0] === 'status' ? 0 : 1];
  };
};

it('records path, relay, handshake and path changes, and turns counters into rates', async () => {
  const reader = new TailnetReader(
    config,
    scripted(
      [status(peer('86.0.250.169:48077')), counters(10, 1, 100, 50)],
      [status(peer('86.0.250.169:48077')), counters(40, 1, 400, 250)],
      [status(peer('', { PeerRelay: '' })), counters(70, 2, 500, 250)],
    ),
  );
  const read = async (at: number) => {
    const host: Record<string, Measurement> = {};
    return { record: await reader.read(at, host, true), host };
  };
  const first = await read(100);
  expect(first.record.home_relay).toBe('fra');
  const [direct] = first.record.peers;
  expect(direct).toMatchObject({
    identity: 'khushals-macbook-pro',
    path: 'direct',
    endpoint: '86.0.250.169:48077',
    relay: 'par',
    last_handshake: Date.parse('2026-11-27T03:08:04.224286885+01:00') / 1000,
  });
  expect(direct!.metrics['tailnet.direct']!.value).toBe(1);
  expect(direct!.metrics['tailnet.path_changes']!.value).toBeNull();
  expect(first.host['tailnet.relay_packets_per_second']!.value).toBeNull();

  const second = await read(115);
  expect(second.record.peers[0]!.metrics['tailnet.path_changes']!.value).toBe(0);
  expect(second.host['tailnet.path_discovery_per_second']!.value).toBe(2);
  expect(second.host['tailnet.relay_packets_per_second']!.value).toBeCloseTo(500 / 15);

  const third = await read(130);
  expect(third.record.peers[0]).toMatchObject({ path: 'relayed', endpoint: null });
  expect(third.record.peers[0]!.metrics['tailnet.direct']!.value).toBe(0);
  expect(third.record.peers[0]!.metrics['tailnet.path_changes']!.value).toBe(1);
  expect(third.host['tailnet.home_relay_changes_per_second']!.value).toBeCloseTo(1 / 15);
});

it('reports a hung command as unavailable within the time limit', async () => {
  const hang = () =>
    new ChildProcesses().spawn('sleep', ['30'], {
      stdio: ['ignore', 'pipe', 'ignore'],
    }) as ReturnType<ReturnType<typeof launcher>>;
  const reader = new TailnetReader(config, (args, timeout, bytes) =>
    query_tailscale(args, timeout, bytes, hang),
  );
  const host: Record<string, Measurement> = {};
  const began = performance.now();
  const record = await reader.read(1, host, true);
  expect(performance.now() - began).toBeLessThan(1000);
  expect(record).toMatchObject({ availability: 'unavailable', reason: 'ETIMEDOUT', peers: [] });
  expect(host['tailnet.relay_packets_per_second']).toMatchObject({
    value: null,
    reason: 'ETIMEDOUT',
  });
});
