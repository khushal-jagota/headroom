/** Pure filename, quota, binding, and expiry decisions. */
import { extname } from 'node:path';
import type { UploadDestination } from '../contracts.ts';
import { VideoUploadError } from '../contracts.ts';
export function validate_filename(filename: string): void {
  if (
    !filename ||
    [...filename].length > 255 ||
    ['.', '..'].includes(filename) ||
    [...filename].some((character) => character.codePointAt(0)! < 32) ||
    filename.includes('/') ||
    filename.includes('\\') ||
    !['.mp4', '.mov', '.webm'].includes(extname(filename).toLowerCase())
  )
    throw new VideoUploadError('Choose an MP4, MOV, or WebM file with a plain filename.');
}
export function require_capacity(used: number, requested: number, limit: number): void {
  if (used + requested > limit)
    throw new VideoUploadError('Video storage is full. Remove this attachment to continue.', 507);
}
export function expiry_eligible(state: string, expires_at: number, now: number): boolean {
  return (
    ['reserved', 'transferring', 'ready', 'failed', 'cancelled'].includes(state) &&
    expires_at <= now
  );
}
export function validate_binding(stored: UploadDestination, current: UploadDestination): void {
  if (
    stored.kind !== current.kind ||
    stored.id !== current.id ||
    stored.expected_conversation_id !== current.expected_conversation_id
  ) {
    throw new VideoUploadError('The conversation changed. Remove and attach the video again.', 409);
  }
}
