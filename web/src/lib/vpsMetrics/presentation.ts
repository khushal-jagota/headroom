import type { Instrument, LatestResponse, Measurement, ProcessRecord, Reading, Sample, ServiceRecord, WorkloadRow } from './contracts';
import { metricsConfig } from './config';
const thresholds = metricsConfig.thresholds;
export function reading(metrics: Record<string, Measurement>, key: string): Reading {
  const m = metrics[key];
  return m ? { value: Number.isFinite(m.value) ? m.value : null, unit: m.unit, reason: m.reason } : { value: null, unit: '', reason: 'Unavailable' };
}
export function formatBytes(value: number | null): string {
  if (value === null) return 'Unavailable';
  const magnitude = Math.abs(value);
  const [divisor, suffix] = magnitude >= 1e12 ? [1e12, 'TB'] : magnitude >= 1e9 ? [1e9, 'GB'] : magnitude >= 1e6 ? [1e6, 'MB'] : magnitude >= 1e3 ? [1e3, 'KB'] : [1, 'B'];
  return `${(value / divisor).toFixed(divisor === 1 ? 0 : 1)} ${suffix}`;
}
export const formatRate = (value: number | null): string => value === null ? 'Unavailable' : `${formatBytes(value)}/s`;
export const formatPercent = (value: number | null): string => value === null ? 'Unavailable' : `${value.toFixed(1)}%`;
export function formatUptime(value: number | null): string {
  if (value === null) return 'uptime unavailable';
  const days = Math.floor(value / 86400), hours = Math.floor(value % 86400 / 3600), minutes = Math.floor(value % 3600 / 60);
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}
export function cpuShare(value: Reading, cores: number): Reading {
  return cores > 0 ? { ...value, value: value.value === null ? null : value.value / cores, unit: '%' } : { value: null, unit: '%', reason: 'Core count unavailable' };
}
export function subtract(total: Reading, available: Reading): Reading {
  return { value: total.value === null || available.value === null ? null : total.value - available.value, unit: total.unit, reason: total.value === null ? total.reason : available.value === null ? available.reason : null };
}
export function freshness(latest: LatestResponse | null, now: number, failed: boolean): { stale: boolean; age: number | null; label: string } {
  const timestamp = latest?.collection.last_sample_at;
  const age = timestamp == null ? null : Math.max(0, now - timestamp);
  const stale = failed || age === null || age > (latest?.collection.cadence_seconds ?? 2) * metricsConfig.staleCadences || latest?.collection.availability === 'unavailable';
  return { stale, age, label: !stale ? 'now' : age === null ? 'no feed' : `${age < 60 ? `${Math.floor(age)}s` : age < 3600 ? `${Math.floor(age / 60)}m` : `${Math.floor(age / 3600)}h`} ago · no feed` };
}
export function instruments(sample: Sample): Instrument[] {
  const get = (key: string) => reading(sample.metrics, key);
  const cpu = get('cpu_percent'), cpuWait = get('pressure.cpu.some.avg10');
  const total = get('memory_total_bytes'), free = get('memory_available_bytes'), memory = subtract(total, free);
  const load = get('load_1'), ioWait = get('pressure.io.some.avg10');
  const panels = sample.services.find(s => s.identity === 'panels-live' || s.name === 'panels-live');
  const panelsCpu = panels ? cpuShare(reading(panels.metrics, 'cpu_percent'), sample.core_count) : null;
    const result: Instrument[] = [
    { id: 'cpu', title: 'CPU', ceiling: 100, primary: { ...cpu, unit: '%' }, secondary: null, waiting: cpuWait, details: [`of ${sample.core_count > 0 ? sample.core_count : 'unavailable'} cores`, ...(panelsCpu ? [`Panels ${formatPercent(panelsCpu.value)}`] : [])], severity: (cpuWait.value ?? -Infinity) > thresholds.cpuWaiting ? 'wrong' : (cpu.value ?? -Infinity) > thresholds.cpuBusy ? 'raised' : 'normal', pill: null },
    { id: 'memory', title: 'Memory', ceiling: total.value, primary: memory, secondary: null, waiting: get('pressure.memory.some.avg10'), details: [`of ${formatBytes(total.value)}`, `${formatBytes(free.value)} free`, get('swap_total_bytes').value === 0 ? 'no swap' : `${formatBytes(get('swap_total_bytes').value)} swap`, ...(panels ? [`Panels ${formatBytes(reading(panels.metrics, 'memory_bytes').value)}`] : [])], severity: free.value !== null && free.value < thresholds.memoryWrong ? 'wrong' : free.value !== null && free.value < thresholds.memoryRaised ? 'raised' : 'normal', pill: null },
    { id: 'load', title: 'Load', primary: load, secondary: null, details: [`${get('load_5').value?.toFixed(2) ?? '—'} at 5 min · ${get('load_15').value?.toFixed(2) ?? '—'} at 15 min`, `${sample.core_count > 0 ? sample.core_count : '—'} cores`], severity: sample.core_count > 0 && load.value !== null && load.value > sample.core_count * thresholds.loadWrong ? 'wrong' : sample.core_count > 0 && load.value !== null && load.value > sample.core_count ? 'raised' : 'normal', pill: null },
    { id: 'disk', title: 'Disk', ceiling: 100, primary: diskFullness(get('filesystem.total_bytes'), get('filesystem.available_bytes')), secondary: null, details: [`${formatBytes(get('filesystem.available_bytes').value)} free of ${formatBytes(get('filesystem.total_bytes').value)}`], severity: 'normal', pill: null },
    { id: 'io', title: 'Disk I/O', primary: get('disk.write_bytes_per_second'), secondary: get('disk.read_bytes_per_second'), waiting: ioWait, details: [], severity: (ioWait.value ?? -Infinity) > thresholds.ioWrong ? 'wrong' : (ioWait.value ?? -Infinity) > thresholds.ioRaised ? 'raised' : 'normal', pill: null },
    { id: 'network', title: 'Network', primary: get('network.receive_bytes_per_second'), secondary: get('network.transmit_bytes_per_second'), details: ['in / out'], severity: 'normal', pill: null }
  ];
  for (const item of result) item.pill = item.severity === 'normal' ? null : item.id === 'cpu' ? item.severity === 'wrong' ? 'Work is waiting' : 'Busy' : item.id === 'memory' ? item.severity === 'wrong' ? 'Nearly full' : 'Filling' : item.id === 'load' ? `Over ${sample.core_count} cores` : item.severity === 'wrong' ? 'Reads are waiting' : 'Waiting';
  const order = ['cpu', 'memory', 'load', 'io', 'network', 'disk'];
  result.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  return result;
}
function stable(rows: WorkloadRow[], previousIds: string[]): WorkloadRow[] {
  const positions = new Map(previousIds.map((id, index) => [id, index]));
  return rows.sort((a, b) => {
    const pa = positions.get(a.id), pb = positions.get(b.id);
    return pa !== undefined || pb !== undefined ? (pa ?? Infinity) - (pb ?? Infinity) : (b.cpu.value ?? -Infinity) - (a.cpu.value ?? -Infinity) || a.name.localeCompare(b.name);
  });
}
export function serviceRows(sample: Sample, previousIds: string[] = []): WorkloadRow[] {
  const rows = sample.services.map((service: ServiceRecord): WorkloadRow => {
    const memory = reading(service.metrics, 'memory_bytes'), total = reading(sample.metrics, 'memory_total_bytes');
    return { id: service.identity, name: service.name, count: service.process_count, cpu: cpuShare(reading(service.metrics, 'cpu_percent'), sample.core_count), memory, waiting: reading(service.metrics, 'pressure.cpu.some.avg10'), share: memory.value !== null && total.value !== null && total.value > 0 ? memory.value / total.value : null, partial: false, state: service.state };
  });
  // Services retain source order initially, then retain their existing positions.
  const order = [...previousIds, ...rows.map(row => row.id).filter(id => !previousIds.includes(id))];
  return stable(rows, order);
}
export function processRows(records: ProcessRecord[], cores: number, previousIds: string[] = []): WorkloadRow[] {
  const groups = new Map<string, ProcessRecord[]>();
  for (const record of records) groups.set(record.name, [...groups.get(record.name) ?? [], record]);
  const rows = [...groups].map(([name, members]): WorkloadRow => {
    const sum = (key: string): Reading => {
      const values = members.map(member => reading(member.metrics, key));
      const usable = values.filter(value => value.value !== null);
      return { value: usable.length ? usable.reduce((total, value) => total + value.value!, 0) : null, unit: values[0].unit, reason: usable.length === members.length ? null : usable.length ? `${members.length - usable.length} unavailable` : values.find(value => value.reason)?.reason ?? 'Unavailable' };
    };
    const cpu = cpuShare(sum('cpu_percent'), cores), memory = sum('rss_bytes');
    return { id: name, name, count: members.length, cpu, memory, waiting: null, share: null, partial: !!cpu.reason || !!memory.reason, state: null };
  });
  const maximum = Math.max(0, ...rows.map(row => row.cpu.value ?? 0));
  for (const row of rows) row.share = row.cpu.value === null ? null : maximum > 0 ? row.cpu.value / maximum : 0;
  return stable(rows, previousIds);
}

export function diskFullness(total: Reading, available: Reading): Reading {
  const used = subtract(total, available);
  return { value: used.value === null || total.value === null || total.value <= 0 ? null : used.value / total.value * 100, unit: '%', reason: used.reason ?? (total.value === null || total.value <= 0 ? 'Disk capacity unavailable' : null) };
}
export function machineDescription(sample: Sample): string {
  const get = (key: string) => reading(sample.metrics, key);
  return `${sample.core_count > 0 ? sample.core_count : 'Unavailable'} cores · ${formatBytes(get('memory_total_bytes').value)} memory · ${formatBytes(get('filesystem.total_bytes').value)} disk · up ${formatUptime(get('uptime_seconds').value)}`;
}
export function formatReading(read: Reading): { value: string; unit: string } {
  if (read.value === null) return { value: read.reason || 'Unavailable', unit: '' };
  const text = read.unit.includes('second') || read.unit === 'bytes/s' || read.unit === 'B/s' ? formatRate(read.value) : read.unit === 'bytes' || read.unit === 'B' ? formatBytes(read.value) : read.unit === '%' || read.unit === 'percent' ? formatPercent(read.value) : read.value.toFixed(2);
  const match = text.match(/^([\d.,-]+)\s*(.*)$/);
  return match ? { value: match[1], unit: match[2] } : { value: text, unit: '' };
}
