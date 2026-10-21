import { parseMarkdown } from '../markdownPipeline';
import type { MarkdownParseReply, MarkdownParseRequest } from './contracts';
import { parseViewRowBody } from '../conversation/view/validation';

self.onmessage = (event: MessageEvent<MarkdownParseRequest>) => {
  const { id, source, kind } = event.data;
  let reply: MarkdownParseReply;
  try {
    reply = kind === 'markdown'
      ? { id, document: parseMarkdown(source) }
      : { id, body: parseViewRowBody(JSON.parse(source)) };
  } catch (error) { reply = { id, error: error instanceof Error ? error.message : String(error) }; }
  self.postMessage(reply);
};
