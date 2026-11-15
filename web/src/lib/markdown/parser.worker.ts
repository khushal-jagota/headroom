import { parseMarkdown } from '../markdownPipeline';
import type { MarkdownParseReply, MarkdownParseRequest } from './contracts';
import type { ViewRowBody } from '../conversation/view/contracts';

self.onmessage = (event: MessageEvent<MarkdownParseRequest>) => {
  const { id, source, kind } = event.data;
  let reply: MarkdownParseReply;
  try {
    reply = kind === 'markdown'
      ? { id, document: parseMarkdown(source) }
      : { id, body: JSON.parse(source) as ViewRowBody };
  } catch (error) { reply = { id, error: error instanceof Error ? error.message : String(error) }; }
  self.postMessage(reply);
};
