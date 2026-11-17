/**
 * Risk: moving the legacy-image-block rule out of conversation/tool_detail.ts loses it or lets it
 * reach another backend. Gap: no TypeScript test covered tool_detail. Layer: pure unit.
 */
import { describe, expect, it } from 'vitest';
import { readable_tool_call_detail } from '../../conversation/tool_detail.ts';
import { parse_backend_instance_id } from '../core/identity.ts';
import { claude_readable_tool_call_detail } from './tool_detail.ts';

const image = (data = 'iVBORw0KGgo=') => ({
  type: 'image',
  source: { type: 'base64', media_type: 'image/png', data },
});
const legacy = JSON.stringify([image(), image()]);

describe('claude_readable_tool_call_detail', () => {
  it('gives null for a detail that is only legacy image blocks', () => {
    expect(claude_readable_tool_call_detail(legacy)).toBeNull();
  });

  it('passes other JSON and non-JSON through', () => {
    for (const detail of [
      'plain output',
      '[]',
      '{"type":"image"}',
      JSON.stringify([image(), { type: 'text', text: 'hi' }]),
      JSON.stringify([image('')]),
      JSON.stringify([{ ...image(), extra: 1 }]),
      JSON.stringify([{ type: 'image', source: { ...image().source, media_type: 'text/plain' } }]),
    ])
      expect(claude_readable_tool_call_detail(detail)).toBe(detail);
  });

  it('reaches only the claude key through readable_tool_call_detail', () => {
    const read = (backend_key: string | null) =>
      readable_tool_call_detail(legacy, {
        backend_key: backend_key === null ? null : parse_backend_instance_id(backend_key),
      });
    expect(read('claude')).toBeNull();
    expect(read('codex')).toBe(legacy);
    expect(read(null)).toBe(legacy);
  });
});
