// The opaque-origin review artifact cannot start a module worker. Use the exact
// production parser on these finite local samples, without a separate worker.
import { parseMarkdown } from '../../src/lib/markdownPipeline';
import { parseViewRowBody } from '../../src/lib/conversation/view/validation';
import type { ParsedMarkdown } from '../../src/lib/markdown/contracts';
import type { ViewRowBody } from '../../src/lib/conversation/view/contracts';

export async function parseMarkdownInWorker(source: string, signal: AbortSignal): Promise<ParsedMarkdown> {
  signal.throwIfAborted();
  return parseMarkdown(source);
}
export async function parseDeferredViewBody(source: string, signal: AbortSignal): Promise<ViewRowBody> {
  signal.throwIfAborted();
  return parseViewRowBody(JSON.parse(source));
}
