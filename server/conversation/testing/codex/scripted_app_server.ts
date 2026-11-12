#!/usr/bin/env node
/** Synthetic Codex JSONL executable shared by the Python and TS parity drivers. */
import { appendFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

if (process.argv.includes('--version')) {
  process.stdout.write('codex-cli 0.166.0\n');
  process.exit(0);
}
if (process.argv[2] !== 'app-server') throw new Error('Only scripted app-server mode is supported');
const trace = process.env.UNIT17_TRACE;
if (!trace) throw new Error('The scripted child requires its synthetic trace path');
function send(message: unknown): void {
  process.stdout.write(JSON.stringify(message) + '\n');
}
let first: Record<string, unknown> | null = null;
let exchange: unknown = null;
const answered = new Set<string>();
const identity = { threadId: 'scripted-thread', turnId: 'scripted-turn' };
const reader = createInterface({ input: process.stdin });
reader.on('line', (line) => {
  const message = JSON.parse(line) as Record<string, unknown>;
  appendFileSync(trace, JSON.stringify(message) + '\n');
  if (message.method === 'initialize') {
    send({ id: message.id, result: { userAgent: 'scripted-codex' } });
  } else if (message.method === 'wire/first') {
    first = message;
  } else if (message.method === 'wire/second') {
    if (first === null) throw new Error('The first request must arrive before the second');
    send({ id: message.id, result: { label: 'second', params: message.params } });
    send({ id: first.id, result: { label: 'first', params: first.params } });
    first = null;
  } else if (message.method === 'wire/exchange') {
    exchange = message.id;
    process.stdout.write('not JSON\n');
    send({ method: 'toString', params: null });
    send({ method: 'item/agentMessage/delta', params: { ...identity, itemId: 'message' } });
    send({
      method: 'item/agentMessage/delta',
      params: {
        ...identity,
        itemId: 'message',
        delta: 'visible ☃',
        added: true,
      },
    });
    send({ id: 'unknown-ask', method: 'unconsumed/request', params: {} });
    send({ id: 'malformed-ask', method: 'item/fileChange/requestApproval', params: {} });
    send({
      id: 'approval-ask',
      method: 'item/commandExecution/requestApproval',
      params: {
        ...identity,
        itemId: 'tool',
        command: 'printf synthetic',
        cwd: '/synthetic',
      },
    });
  } else if (message.method === 'wire/slow-handler') {
    send({
      method: 'item/agentMessage/delta',
      params: { ...identity, itemId: 'slow', delta: 'slow' },
    });
    send({ id: message.id, result: { after_handler: true } });
  } else if (message.method === 'account/rateLimits/read') {
    process.stdout.write(
      `{"id":${JSON.stringify(message.id)},"result":{"rateLimits":{"primary":{"usedPercent":25.0,"windowDurationMins":300,"resetsAt":2000000000}},"note":${JSON.stringify('literal result } and "quoted"')}}}\n`,
    );
  } else if (message.method === 'wire/reject') {
    send({
      id: message.id,
      error: { code: 403, message: 'scripted refusal', data: { why: 'policy' } },
    });
  } else if (message.method === 'wire/exit') {
    process.stderr.write('scripted child exit\n', () => process.exit(7));
  } else if (typeof message.id === 'string' && exchange !== null) {
    answered.add(message.id);
    if (answered.size === 3) {
      send({
        id: exchange,
        result: { answered: ['unknown-ask', 'malformed-ask', 'approval-ask'] },
      });
      exchange = null;
    }
  }
});
