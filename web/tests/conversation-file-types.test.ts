import { describe, expect, it } from 'vitest';
import { fileOpensAsText, friendlyFileType } from '../src/lib/conversation/fileTypes';

describe('file type names', () => {
  it('calls a file by its friendly name, never its media type', () => {
    expect(friendlyFileType('export-2026-v1.csv', 'text/csv')).toBe('CSV');
    expect(friendlyFileType('ci-run-4812.txt', 'text/plain')).toBe('Text');
    expect(friendlyFileType('brief.pdf')).toBe('PDF');
    expect(friendlyFileType('shot.PNG', 'image/png')).toBe('PNG');
    expect(friendlyFileType('notes', 'text/plain; charset=utf-8')).toBe('Text');
    expect(friendlyFileType('blob', 'application/octet-stream')).toBe('File');
    expect(friendlyFileType('.env')).toBe('File');
  });

  it('offers a preview only for files the browser shows as text or a document', () => {
    expect(fileOpensAsText('ci-run-4812.txt', 'text/plain')).toBe(true);
    expect(fileOpensAsText('brief.pdf', 'application/pdf')).toBe(true);
    expect(fileOpensAsText('archive.zip', 'application/zip')).toBe(false);
    expect(fileOpensAsText('voice.m4a', 'audio/mp4')).toBe(false);
  });
});
