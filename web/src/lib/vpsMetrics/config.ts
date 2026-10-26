import type { WindowId } from './contracts';
export const metricsConfig = {
  pollMilliseconds: 2000, readConcurrency: 2, processPageRows: 250,
  staleCadences: 2, rawGapSeconds: 30, clockMilliseconds: 1000, bytesPerGB: 1e9,
  thresholds: { cpuBusy: 80, cpuWaiting: 40, memoryRaised: 5e9, memoryWrong: 2e9, loadWrong: 1.5, ioRaised: 2, ioWrong: 15 },
  windows: {
    '5m': { label: '5 minutes', seconds: 300, resolution: 'raw', refreshSeconds: 0, chunkSeconds: 300 },
    '24h': { label: '24 hours', seconds: 86400, resolution: 'minute', refreshSeconds: 60, chunkSeconds: 86400 },
    '30d': { label: '30 days', seconds: 2592000, resolution: 'coarse', refreshSeconds: 900, chunkSeconds: 1296000 }
  } as const satisfies Record<WindowId, unknown>
};
export const historySignals = ['cpu_percent', 'memory_total_bytes', 'memory_available_bytes', 'load_1', 'filesystem.total_bytes', 'filesystem.available_bytes', 'disk.read_bytes_per_second', 'disk.write_bytes_per_second', 'network.receive_bytes_per_second', 'network.transmit_bytes_per_second'] as const;
