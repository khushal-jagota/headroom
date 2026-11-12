/** The route-required binding rule; intake budgets and expiry scans remain in Unit 19. */
import type { UploadDestination } from '../contracts.ts';
import { VideoUploadError } from '../contracts.ts';
export function validate_binding(stored: UploadDestination, current: UploadDestination): void {
  if (
    stored.kind !== current.kind ||
    stored.id !== current.id ||
    stored.expected_conversation_id !== current.expected_conversation_id
  ) {
    throw new VideoUploadError('The conversation changed. Remove and attach the video again.', 409);
  }
}
