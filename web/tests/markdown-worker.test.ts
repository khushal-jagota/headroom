import { afterEach, expect, it, vi } from 'vitest';
import { parseMarkdownInWorker, parseDeferredViewBody } from '../src/lib/markdown/workerClient';
import { parseMarkdown } from '../src/lib/markdownPipeline';
import type { MarkdownParseReply, MarkdownParseRequest } from '../src/lib/markdown/contracts';

class FakeWorker {
  static instances: FakeWorker[] = [];
  onmessage: ((event: MessageEvent<MarkdownParseReply>) => void) | null = null;
  onerror: (() => void) | null = null;
  calls: MarkdownParseRequest[] = [];
  terminated = false;
  constructor() { FakeWorker.instances.push(this); }
  postMessage(request: MarkdownParseRequest) { this.calls.push(request); }
  terminate() { this.terminated = true; }
  reply(reply: MarkdownParseReply) { this.onmessage?.({ data: reply } as MessageEvent<MarkdownParseReply>); }
}
afterEach(() => { vi.unstubAllGlobals(); FakeWorker.instances = []; });

it('parses exact GFM with sanitized literal HTML, references, images and source tokens without a DOM', () => {
  const parsed = parseMarkdown('# Heading\n\n[reference][r]\n\n![image](/x.png)\n\n| A | B |\n| - | - |\n| a | b |\n\n<script>alert(1)</script>\n\n[r]: /target');
  const value = JSON.stringify(parsed);
  expect(value).toContain('reference'); expect(value).toContain('dataMarkdownSourceToken');
  expect(value).toContain('table'); expect(value).toContain('alert(1)');
  expect(value).not.toContain('"tagName":"script"');
  expect(parsed.definitions).toMatchObject([{ identifier: 'r', url: '/target' }]);
});

it('cancels one owner, fairly advances remaining work, and rejects obsolete worker events', async () => {
  vi.stubGlobal('Worker', FakeWorker);
  const one = new AbortController(); const two = new AbortController(); const three = new AbortController();
  const first = parseMarkdownInWorker('first', one.signal).catch(error => error.name);
  const second = parseMarkdownInWorker('second', two.signal).catch(error => error.name);
  const third = parseDeferredViewBody('{"kind":"agent_message"}', three.signal);
  const oldWorker = FakeWorker.instances[0];
  expect(oldWorker.calls).toHaveLength(1);
  two.abort(); expect(await second).toBe('AbortError');
  one.abort(); expect(await first).toBe('AbortError');
  expect(oldWorker.terminated).toBe(true);
  const worker = FakeWorker.instances[1];
  expect(worker.calls[0].kind).toBe('view-body');
  oldWorker.onerror?.();
  expect(worker.terminated).toBe(false);
  const body = { kind: 'agent_message' as const, content: [], to_owner: false };
  worker.reply({ id: worker.calls[0].id, body });
  expect(await third).toEqual(body);
  expect(worker.terminated).toBe(true);
});
