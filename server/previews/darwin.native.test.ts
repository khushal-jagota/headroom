/** Hosted Mac acceptance of the real libproc socket ABI and native resource reader. */
import { expect, test } from 'vitest';
import { createServer, createConnection } from 'node:net';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { DarwinProcessInspector } from './darwin.ts';
import { DarwinCollector } from '../environments/vps_metrics/collectors/darwin.ts';
import { DEFAULT_METRICS_CONFIG } from '../environments/vps_metrics/configuration.ts';

test.skipIf(process.env.GITHUB_ACTIONS !== 'true' || process.platform !== 'darwin')(
  'native Mac preview pins its owned listener and accepted connection, and records native samples',
  async () => {
    const began = performance.now();
    const server = createServer(), accepted = once(server, 'connection');
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Missing owned listener');
    const inspector = new DarwinProcessInspector();
    let client: ReturnType<typeof createConnection> | undefined;
    let peer: import('node:net').Socket | undefined;
    try {
      const identity = await inspector.discover(address.port, process.cwd());
      expect(identity.pid).toBe(process.pid);
      client = createConnection(address.port, '127.0.0.1');
      const connected = once(client, 'connect');
      [peer] = await accepted;
      await connected;
      await inspector.connected(identity, client.localPort!);
      const collector = new DarwinCollector(DEFAULT_METRICS_CONFIG);
      const first = await collector.collect({ include_slow: true });
      await delay(25);
      const second = await collector.collect({ include_slow: false });
      expect(first.metrics.memory_total_bytes!.value).toBeGreaterThan(0);
      expect(first.metrics['filesystem.total_bytes']!.value).toBeGreaterThan(0);
      expect(second.metrics.cpu_percent!.value).not.toBeNull();
      expect(second.processes.some((value) => value.identity.pid === process.pid)).toBe(true);
      expect(second.metrics['pressure.cpu.some.avg10']!.value).toBeNull();
      expect(second.services).toEqual([]);
      await new Promise<void>((resolve) => { peer!.destroy(); client!.destroy(); server.close(() => resolve()); });
      await expect(inspector.check(identity)).rejects.toThrow();
    } finally {
      peer?.destroy(); client?.destroy();
      if (server.listening) await new Promise<void>((resolve) => server.close(() => resolve()));
      console.info(`Darwin preview and metrics native ABI: ${Math.round(performance.now() - began)}ms`);
    }
  }, 15_000,
);
