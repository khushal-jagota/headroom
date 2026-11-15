import type { MarkdownParseReply, MarkdownParseRequest, ParsedMarkdown } from './contracts';
import type { ViewRowBody } from '../conversation/view/contracts';

type Result = ParsedMarkdown | ViewRowBody;
type Job = { id: number; source: string; kind: MarkdownParseRequest['kind'];
  resolve: (document: Result) => void; reject: (error: Error) => void; cleanup: () => void };
let sequence = 0;
let active: Job | null = null;
let worker: Worker | null = null;
const waiting: Job[] = [];

function finish(error?: Error, document?: Result): void {
  const job = active;
  active = null;
  if (job) {
    job.cleanup(); job.source = '';
    if (error) job.reject(error);
    else job.resolve(document!);
  }
  startNext();
}
function releaseWorker(): void { worker?.terminate(); worker = null; }
function startNext(): void {
  if (active) return;
  const job = waiting.shift();
  if (!job) { releaseWorker(); return; }
  active = job;
  try {
    worker ??= new Worker(new URL('./parser.worker.ts', import.meta.url), { type: 'module' });
    const owner = worker;
    owner.onmessage = (event: MessageEvent<MarkdownParseReply>) => {
      if (owner !== worker || event.data.id !== active?.id) return;
      if ('error' in event.data) finish(new Error(event.data.error));
      else if (job.kind === 'markdown' && 'document' in event.data) finish(undefined, event.data.document);
      else if (job.kind === 'view-body' && 'body' in event.data) finish(undefined, event.data.body);
      else finish(new Error('Unexpected text parser response.'));
    };
    owner.onerror = () => {
      if (owner !== worker) return;
      releaseWorker(); finish(new Error('Text preview could not load.'));
    };
    owner.postMessage({ id: job.id, source: job.source, kind: job.kind } satisfies MarkdownParseRequest);
    job.source = '';
  } catch (error) {
    releaseWorker(); finish(error instanceof Error ? error : new Error(String(error)));
  }
}

/** FIFO ownership, one parser scratch heap, and actual cancellation of obsolete work. */
function parse(source: string, signal: AbortSignal, kind: MarkdownParseRequest['kind']): Promise<Result> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const abort = () => {
      if (active === job) {
        releaseWorker(); finish(new DOMException('Aborted', 'AbortError'));
      } else {
        const index = waiting.indexOf(job);
        if (index >= 0) waiting.splice(index, 1);
        job.cleanup(); job.source = ''; reject(new DOMException('Aborted', 'AbortError'));
      }
    };
    const job: Job = { id: ++sequence, source, kind, resolve, reject,
      cleanup: () => signal.removeEventListener('abort', abort) };
    signal.addEventListener('abort', abort, { once: true });
    waiting.push(job); startNext();
  });
}
export function parseMarkdownInWorker(source: string, signal: AbortSignal): Promise<ParsedMarkdown> {
  return parse(source, signal, 'markdown') as Promise<ParsedMarkdown>;
}
export function parseDeferredViewBody(source: string, signal: AbortSignal): Promise<ViewRowBody> {
  return parse(source, signal, 'view-body') as Promise<ViewRowBody>;
}
