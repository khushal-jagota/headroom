/** Readable tool output shared by public conversation reads. */
import type { BackendInstanceId } from '../drivers/core/identity.ts';
import { is_record as object } from '../core/records.ts';
function legacy_image_block(value: unknown): boolean {
  if (
    !object(value) ||
    Object.keys(value).sort().join(',') !== 'source,type' ||
    value.type !== 'image'
  ) {
    return false;
  }
  const source = value.source;
  return (
    object(source) &&
    Object.keys(source).sort().join(',') === 'data,media_type,type' &&
    source.type === 'base64' &&
    typeof source.media_type === 'string' &&
    source.media_type.startsWith('image/') &&
    typeof source.data === 'string' &&
    Boolean(source.data)
  );
}
export function readable_tool_call_detail(
  detail: string | null,
  options: {
    backend_key: BackendInstanceId;
  },
): string | null {
  const { backend_key } = options;
  if (detail === null || backend_key !== 'claude') {
    return detail;
  }
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
