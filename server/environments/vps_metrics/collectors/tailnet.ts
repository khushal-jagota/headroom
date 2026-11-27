/** The network path to each tailnet device, from `tailscale status --json` and `debug metrics`. */
import type { MetricsConfig } from '../configuration.ts';
import type { Measurement, TailnetPath, TailnetPeer, TailnetRecord } from '../contracts.ts';
import { float } from '../codec.ts';
import { counter_rate } from '../logic/rates.ts';
import type { CollectorDependencies } from './contracts.ts';
import { evidence_error, source_error } from './errors.ts';

type Metrics = Record<string, Measurement>;
const failure_codes = ['EIO', 'TypeError', 'ETIMEDOUT', 'ENOENT', 'EACCES'];
/** Host counters: path discovery attempts, home relay changes, and packets that used a relay. */
const COUNTERS = {
  'tailnet.path_discovery_per_second': ['magicsock_disco_sent_callmemaybe'],
  'tailnet.home_relay_changes_per_second': ['derp_home_change'],
  'tailnet.relay_packets_per_second': ['magicsock_send_data_derp', 'magicsock_recv_data_derp'],
} as const;
const COUNTER_UNITS: Record<keyof typeof COUNTERS, string> = {
  'tailnet.path_discovery_per_second': 'attempts_per_second',
  'tailnet.home_relay_changes_per_second': 'changes_per_second',
  'tailnet.relay_packets_per_second': 'packets_per_second',
};
const measure = (
  value: number | null,
  unit: string,
  reason: string | null = null,
): Measurement => ({
  value: value === null ? null : float(value),
  unit,
  reason,
});
interface StatusPeer {
  ID?: string;
  HostName?: string;
  DNSName?: string;
  Online?: boolean;
  Active?: boolean;
  CurAddr?: string;
  Relay?: string;
  PeerRelay?: string;
  LastHandshake?: string;
}
interface PeerReading extends Omit<TailnetPeer, 'metrics'> {
  active_path: string | null;
}
function peer_reading(peer: StatusPeer): PeerReading {
  const name = peer.DNSName?.split('.')[0] || peer.HostName || '';
  if (!name) throw new TypeError('tailnet peer has no name');
  const path: TailnetPath =
    peer.Online === false
      ? 'offline'
      : peer.CurAddr
        ? 'direct'
        : peer.PeerRelay || peer.Active
          ? 'relayed'
          : 'idle';
  const handshake = peer.LastHandshake ? Date.parse(peer.LastHandshake) : NaN;
  const endpoint = path === 'direct' ? peer.CurAddr! : null;
  return {
    identity: name.toLowerCase(),
    name,
    path,
    endpoint,
    relay: peer.PeerRelay || peer.Relay || null,
    // Go's zero time is year 1, which parses to a negative number.
    last_handshake: handshake > 0 ? float(handshake / 1000) : null,
    active_path: path === 'direct' || path === 'relayed' ? `${path}:${endpoint ?? ''}` : null,
  };
}
export function parse_status(text: string): { home_relay: string | null; peers: PeerReading[] } {
  const status = JSON.parse(text) as {
    Self?: StatusPeer;
    Peer?: Record<string, StatusPeer> | null;
  };
  if (typeof status !== 'object' || status === null || !status.Self) throw new TypeError('no Self');
  return {
    home_relay: status.Self.Relay || null,
    peers: Object.values(status.Peer ?? {}).map(peer_reading),
  };
}
export function parse_counters(text: string): Record<string, number> {
  const values: Record<string, number> = {};
  for (const row of text.split('\n')) {
    const [name, value] = row.split(' ');
    if (name && value !== undefined && !name.startsWith('#')) values[name] = Number(value);
  }
  return values;
}
export class TailnetReader {
  private readonly config: MetricsConfig;
  private readonly query: CollectorDependencies['tailscale_query'];
  private counters: Record<string, number> | null = null;
  private counters_at = 0;
  private active_paths = new Map<string, string>();
  constructor(config: MetricsConfig, query: CollectorDependencies['tailscale_query']) {
    this.config = config;
    this.query = query;
  }
  private async run(args: string[]): Promise<string> {
    return await this.query(
      args,
      this.config.tailnet_query_seconds,
      this.config.tailnet_output_bytes,
    );
  }
  /** Never rejects, and takes at most the query time limit. */
  async read(monotonic: number, host_metrics: Metrics, same_boot: boolean): Promise<TailnetRecord> {
    const [status, counters] = await Promise.allSettled([
      this.run(['status', '--json']),
      this.run(['debug', 'metrics']),
    ]);
    this.host_counters(counters, monotonic, host_metrics, same_boot);
    try {
      if (status.status === 'rejected') throw status.reason;
      const parsed = parse_status(status.value);
      return {
        availability: 'available',
        reason: null,
        home_relay: parsed.home_relay,
        peers: parsed.peers.map((peer) => this.peer(peer)),
      };
    } catch (error) {
      const reason = source_error(
        error instanceof SyntaxError ? evidence_error('TypeError', error.message) : error,
        failure_codes,
      );
      return { availability: 'unavailable', reason, home_relay: null, peers: [] };
    }
  }
  private peer({ active_path, ...peer }: PeerReading): TailnetPeer {
    const last = this.active_paths.get(peer.identity);
    if (active_path !== null) this.active_paths.set(peer.identity, active_path);
    const inactive = `${peer.path}, no path in use`;
    return {
      ...peer,
      metrics: {
        'tailnet.direct': measure(
          peer.path === 'direct' ? 1 : peer.path === 'relayed' ? 0 : null,
          'boolean',
          active_path === null ? inactive : null,
        ),
        'tailnet.path_changes': measure(
          active_path === null || last === undefined ? null : last === active_path ? 0 : 1,
          'count',
          active_path === null ? inactive : last === undefined ? 'no earlier path seen' : null,
        ),
      },
    };
  }
  private host_counters(
    result: PromiseSettledResult<string>,
    monotonic: number,
    metrics: Metrics,
    same_boot: boolean,
  ): void {
    const names = Object.keys(COUNTERS) as (keyof typeof COUNTERS)[];
    try {
      if (result.status === 'rejected') throw result.reason;
      const values = parse_counters(result.value);
      const current = Object.fromEntries(
        names.map((name) => [
          name,
          COUNTERS[name].reduce((sum, counter) => {
            if (!Number.isFinite(values[counter])) throw new TypeError(`${counter} missing`);
            return sum + values[counter]!;
          }, 0),
        ]),
      );
      for (const name of names)
        metrics[name] = counter_rate(
          current[name]!,
          this.counters?.[name] ?? null,
          monotonic - this.counters_at,
          { same_identity: same_boot, unit: COUNTER_UNITS[name] },
        );
      this.counters = current;
      this.counters_at = monotonic;
    } catch (error) {
      const reason = source_error(error, failure_codes);
      this.counters = null;
      for (const name of names) metrics[name] = measure(null, COUNTER_UNITS[name], reason);
    }
  }
}
