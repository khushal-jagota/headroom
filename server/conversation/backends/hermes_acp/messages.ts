/** ACP message blocks retain authored order and use the core's managed file service. */
import { pathToFileURL } from 'node:url';
import type { ContentBlock } from '@agentclientprotocol/sdk/unstable';
import { MessageFile, MessageImage, MessageText } from '../../message_content.ts';
import type { MessageContent, MessagePiece } from '../../message_content.ts';
import type { ConversationMessageFiles } from '../../message_files.ts';
import { MessageFileMissing } from '../../record/message_files.ts';
import { PromptWriteFailed } from '../contracts.ts';

function file_error(error: unknown): boolean {
  return error instanceof MessageFileMissing || (error instanceof Error && 'code' in error);
}
export class HermesMessages {
  private readonly files: ConversationMessageFiles;
  private readonly conversation_id: string;
  constructor(files: ConversationMessageFiles, conversation_id: string) {
    this.files = files;
    this.conversation_id = conversation_id;
  }
  async prompt_blocks(content: MessageContent): Promise<ContentBlock[]> {
    const blocks: ContentBlock[] = [];
    for (const piece of content) {
      if (piece instanceof MessageText) blocks.push({ type: 'text', text: piece.text });
      else if (piece instanceof MessageImage) {
        try {
          const bytes = await this.files.read(this.conversation_id, piece.stored_file_id);
          blocks.push({
            type: 'image',
            data: Buffer.from(bytes).toString('base64'),
            mimeType: piece.media_type,
          });
        } catch (error) {
          if (!file_error(error)) throw error;
          throw new PromptWriteFailed(`${piece.stored_file_id} could not be read`);
        }
      } else if (piece instanceof MessageFile) {
        try {
          blocks.push({
            type: 'resource_link',
            uri: pathToFileURL(this.files.path_of(this.conversation_id, piece.stored_file_id)).href,
            name: piece.file_name,
            title: piece.file_name,
            mimeType: piece.media_type,
            size: piece.byte_count,
          });
        } catch (error) {
          if (!(error instanceof MessageFileMissing)) throw error;
          throw new PromptWriteFailed(`${piece.stored_file_id} could not be read`);
        }
      }
    }
    return blocks;
  }
  async piece_of(content: ContentBlock): Promise<MessagePiece | null> {
    if (content.type === 'text') return new MessageText({ text: content.text });
    if (content.type !== 'image') return null;
    // Buffer's decoder silently accepts junk. Python uses b64decode(validate=True).
    if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(content.data))
      return null;
    try {
      const kept = await this.files.keep(
        this.conversation_id,
        Buffer.from(content.data, 'base64'),
        { media_type: content.mimeType },
      );
      return new MessageImage({
        stored_file_id: kept.stored_file_id,
        media_type: content.mimeType,
      });
    } catch (error) {
      if (!file_error(error)) throw error;
      return null;
    }
  }
}
