import { expect, test } from 'vitest';
import { AgentProcesses } from '../agent_processes.ts';
import { parse_agent_process_snapshot } from '../platform.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import type { AgentProcessPlatform } from '../platform.ts';

test('process custody refuses reused PIDs and retains reparented tools', async () => {
  let text = JSON.stringify({
    processes: [
      { process_id: 11, started: 'old' },
      { process_id: 12, started: 'previous' },
    ],
  });
  let table = parse_agent_process_snapshot(
    '11 1 S old\n12 1 S reused\n21 11 S descendant\n33 11 Z zombie\n500 1 S host',
  );
  const signals: [number, string][] = [],
    lowered: number[][] = [];
  const platform: AgentProcessPlatform = {
    async snapshot() {
      return table;
    },
    async lower_priority(row, nice) {
      lowered.push([row.process_id, nice]);
      return true;
    },
    async signal(row, signal) {
      signals.push([row.process_id, signal]);
      table = table.filter((value) => value.process_id !== row.process_id);
    },
  };
  const children = new OneShotChildren(new ChildProcesses());
  const agents = new AgentProcesses({
    run: '/synthetic/run',
    parent_process_id: 500,
    platform,
    one_shot_children: children,
    warn() {},
    files: {
      async read_runtime_record() {
        return text;
      },
      async atomic_runtime_record(_, value) {
        text = value;
      },
      async remove_runtime_record() {},
    },
  });
  await agents.end_orphans();
  expect(signals).toEqual([
    [11, 'SIGTERM'],
    [21, 'SIGTERM'],
  ]);
  expect(JSON.parse(text).processes).toEqual([]);
  table = parse_agent_process_snapshot('500 1 S host\n40 500 S agent\n41 40 S tool\n12 1 S reused');
  await agents.record_children();
  expect(lowered).toEqual([
    [40, 10],
    [41, 10],
  ]);
  table = parse_agent_process_snapshot('500 1 S host\n41 1 S tool\n12 1 S reused');
  await agents.record_children();
  expect(JSON.parse(text).processes).toEqual([{ process_id: 41, started: 'tool' }]);
  await agents.stop({ deadline: performance.now() / 1000 + 5 });
  await agents.settled();
  await expect(agents.record_children()).rejects.toThrow('stopped');
  children.seal();
  await children.settled();
});
