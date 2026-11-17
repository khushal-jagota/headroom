/** Claude's readable tool-call detail: old rows can hold image blocks with no readable text. */
import { is_record } from '../../core/records.ts';

function legacy_image_block(value: unknown): boolean {
  if (
    !is_record(value) ||
    Object.keys(value).sort().join(',') !== 'source,type' ||
    value.type !== 'image'
  ) {
    return false;
  }
  const source = value.source;
  return (
    is_record(source) &&
    Object.keys(source).sort().join(',') === 'data,media_type,type' &&
    source.type === 'base64' &&
    typeof source.media_type === 'string' &&
    source.media_type.startsWith('image/') &&
    typeof source.data === 'string' &&
    Boolean(source.data)
  );
}
/** Null when the detail is only base64 image blocks, which hold nothing to show; else as stored. */
export function claude_readable_tool_call_detail(detail: string): string | null {
  let blocks: unknown;
  try {
    blocks = JSON.parse(detail);
  } catch {
    return detail;
  }
  return Array.isArray(blocks) && blocks.length > 0 && blocks.every(legacy_image_block)
    ? null
    : detail;
}
