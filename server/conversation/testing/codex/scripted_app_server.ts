#!/usr/bin/env node
/** Synthetic Codex JSONL executable shared by the Python and TS parity drivers. */
import { appendFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createInterface } from 'node:readline';

if (process.argv.includes('--version')) {
  process.stdout.write('codex-cli 0.166.0\n');
  process.exit(0);
}
if (process.argv[2] !== 'app-server') throw new Error('Only scripted app-server mode is supported');
const trace = process.env.UNIT17_TRACE;
if (!trace) throw new Error('The scripted child requires its synthetic trace path');
let outgoing = Promise.resolve();
function send(message: unknown): void {
  const line = Buffer.from(JSON.stringify(message) + '\n');
  if (process.env.UNIT17_CORE_PROOF !== '1') { process.stdout.write(line); return; }
  outgoing = outgoing.then(async () => {
    const split = line.indexOf(Buffer.from('☃'));
    if (split < 0) { process.stdout.write(line); return; }
    // One frame crosses both a JSONL chunk and a UTF-8 character boundary.
    process.stdout.write(line.subarray(0, split + 1));
    await new Promise((resolve) => setTimeout(resolve, 2));
    process.stdout.write(line.subarray(split + 1));
  });
}
let first: Record<string, unknown> | null = null;
let exchange: unknown = null;
const answered = new Set<string>();
const identity = { threadId: 'scripted-thread', turnId: 'scripted-turn' };

const coreProof = process.env.UNIT17_CORE_PROOF === '1';
const statePath = `${trace}.state.json`;
interface NativeState { nextTurn: number; resumes: number; memory: string }
const nativeState: NativeState = coreProof && existsSync(statePath)
  ? JSON.parse(readFileSync(statePath, 'utf8')) as NativeState
  : { nextTurn: 0, resumes: 0, memory: '' };
const saveNative = () => writeFileSync(statePath, JSON.stringify(nativeState));
const nativeThread = 'unit17-thread';
let nativeTurn = '';
let pendingAsk: number | null = null;
const note = (kind: string) => appendFileSync(`${trace}.children`, JSON.stringify({ kind, pid: process.pid }) + '\n');
if (coreProof) {
  note('launch');
  process.once('exit', () => note('exit'));
  process.once('SIGTERM', () => process.exit(0));
}
function nativeNotification(method: string, extra: Record<string, unknown>): void {
  send({ method, params: { threadId: nativeThread, turnId: nativeTurn, ...extra } });
}
function nativeEnded(status = 'completed', error?: string): void {
  send({ method: 'turn/completed', params: { threadId: nativeThread, turn: {
    id: nativeTurn, status, error: error ? { message: error } : null,
  } } });
}
function nativeText(text: string): void {
  nativeNotification('item/agentMessage/delta', { itemId: `message-${nativeTurn}`, delta: text });
  nativeNotification('item/completed', { item: { type: 'agentMessage', id: `message-${nativeTurn}`, text } });
}
function coreMessage(message: Record<string, unknown>): boolean {
  if (!coreProof) return false;
  const params = (message.params ?? {}) as Record<string, unknown>;
  const answer = (result: unknown) => send({ id: message.id, result });
  if (message.method === 'initialize') answer({ userAgent: 'scripted-codex' });
  else if (message.method === 'initialized') { /* one-way handshake */ }
  else if (message.method === 'thread/start') answer({ thread: { id: nativeThread, ephemeral: false } });
  else if (message.method === 'thread/resume') {
    nativeState.resumes += 1;
    saveNative();
    answer({ thread: { id: nativeState.resumes === 1 ? nativeThread : 'substituted-thread', ephemeral: false } });
  } else if (message.method === 'skills/list') answer({ data: [{ skills: [] }] });
  else if (message.method === 'plugin/installed') answer({ marketplaces: [] });
  else if (message.method === 'app/installed') answer({ apps: [] });
  else if (message.method === 'thread/goal/get') answer({ goal: {
    objective: 'Synthetic objective', status: 'active', timeUsedSeconds: 3, tokenBudget: 99, tokensUsed: 7,
  } });
  else if (message.method === 'thread/compact/start' || message.method === 'review/start') {
    nativeTurn = `turn-${++nativeState.nextTurn}`;
    saveNative();
    answer(message.method === 'review/start' ? { turn: { id: nativeTurn, status: 'inProgress' } } : {});
    send({ method: 'turn/started', params: { threadId: nativeThread, turn: { id: nativeTurn, status: 'inProgress' } } });
    if (message.method === 'thread/compact/start') nativeNotification('item/completed', { item: { type: 'contextCompaction' } });
    else nativeText('review output');
    nativeEnded();
  } else if (message.method === 'turn/start') {
    nativeTurn = `turn-${++nativeState.nextTurn}`;
    saveNative();
    const content = (params.input as { type: string; text?: string }[]).map((piece) => piece.text ?? '').join('\n');
    const scenario = /\[CASE:([^\]]+)\]/.exec(content)?.[1] ?? 'native';
    if (scenario === 'rejected-after-output') {
      const frames = [
        { method: 'skills/changed', params: {} },
        { method: 'item/agentMessage/delta', params: { threadId: nativeThread, turnId: nativeTurn, itemId: 'provisional', delta: 'unadmitted output' } },
        { id: message.id, error: { code: -32000, message: 'rejected after provisional activity' } },
      ];
      outgoing = outgoing.then(() => { process.stdout.write(frames.map((frame) => JSON.stringify(frame)).join('\n') + '\n'); });
      return true;
    }
    answer({ turn: { id: nativeTurn, status: 'inProgress' } });
    send({ method: 'turn/started' , params: { threadId: nativeThread, turn: { id: nativeTurn, status: 'inProgress' } } });
    if (['steer-base', 'queue-base', 'interrupt-base'].includes(scenario)) return true;
    if (scenario === 'permission') {
      pendingAsk = 1000 + nativeState.nextTurn;
      send({ id: pendingAsk, method: 'item/commandExecution/requestApproval', params: {
        threadId: nativeThread, turnId: nativeTurn, itemId: 'command', command: 'printf synthetic', cwd: '/synthetic',
      } });
    } else if (scenario === 'question') {
      pendingAsk = 2000 + nativeState.nextTurn;
      send({ id: pendingAsk, method: 'item/tool/requestUserInput', params: {
        threadId: nativeThread, turnId: nativeTurn, itemId: 'question-tool', questions: [
          { id: 'color', header: 'Color', question: 'Choose a color?', options: [{ label: 'Blue', description: 'Blue color' }] },
        ],
      } });
    } else if (scenario === 'auth-failure' || scenario === 'auth-refresh') {
      if (scenario === 'auth-refresh') {
        const home = process.env.CODEX_HOME;
        if (!home) throw new Error('Synthetic auth refresh requires a synthetic home');
        writeFileSync(join(home, 'auth.json'), JSON.stringify({ tokens: { account_id: 'synthetic-account' }, last_refresh: 'new-generation' }));
      }
      process.stderr.write('codex_login::auth: invalid_grant\n');
      nativeEnded('failed', 'invalid_grant');
    } else {
      if (scenario === 'plain') {
        nativeState.memory = 'codex-memory-token';
        saveNative();
        nativeNotification('item/started', { item: { type: 'reasoning' } });
        nativeNotification('item/started', { item: { type: 'commandExecution', id: 'tool', command: 'printf synthetic', cwd: '/synthetic', status: 'inProgress' } });
        nativeNotification('item/commandExecution/outputDelta', { itemId: 'tool', delta: 'tool output' });
        nativeNotification('item/completed', { item: { type: 'commandExecution', id: 'tool', command: 'printf synthetic', cwd: '/synthetic', status: 'completed', aggregatedOutput: 'tool output' } });
        nativeNotification('turn/plan/updated', { plan: [{ step: 'Synthetic step', status: 'completed' }] });
        nativeNotification('thread/tokenUsage/updated', { tokenUsage: { total: { inputTokens: 11, outputTokens: 5, cachedInputTokens: 2 } } });
      }
      if (scenario === 'malformed') nativeNotification('item/agentMessage/delta', { itemId: 'bad', delta: false });
      nativeText(scenario === 'resume-success' ? nativeState.memory : scenario === 'plain' ? 'plain ☃ output' : `${scenario} output`);
      nativeEnded();
    }
  } else if (message.method === 'turn/steer') {
    answer({ turnId: nativeTurn });
    nativeText('steered output');
    nativeEnded();
  } else if (message.method === 'turn/interrupt') {
    answer({});
    nativeEnded('interrupted');
  } else if (message.id === pendingAsk) {
    pendingAsk = null;
    nativeText('answer received');
    nativeEnded();
  } else throw new Error(`Unsupported core proof method ${String(message.method)}`);
  return true;
}

const reader = createInterface({ input: process.stdin });
reader.on('line', (line) => {
  const message = JSON.parse(line) as Record<string, unknown>;
  appendFileSync(trace, JSON.stringify(message) + '\n');
  if (coreMessage(message)) return;
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

reader.once('close', () => { void outgoing.finally(() => process.exit(0)); });
