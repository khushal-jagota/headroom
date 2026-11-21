import { describe, expect, it } from 'vitest';
import { validated_file_media_type } from '../conversation/file_validation.ts';

describe('conversation file validation', () => {
  it('accepts a UTF-8 .log file as plain text', () => {
    expect(validated_file_media_type(Buffer.from('ok 12 tests\n'), 'build.log')).toBe('text/plain');
  });

  it('refuses a binary .log file', () => {
    expect(() => validated_file_media_type(Buffer.from([0xff, 0xfe, 0x00, 0x80]), 'crash.log')).toThrow();
  });
});
