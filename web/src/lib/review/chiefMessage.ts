import { fetchJson } from '../api';
import type { ChiefManagementSettings } from '../types';

export const chiefDraftKey = 'owner:chief';
export const chiefAttachmentDraftId = 'owner:chief';
export const chiefAttemptKey = 'review-chief-attempt';
export const chiefAttemptMetadataKey = 'panels:review-chief-attempt-destination';

export async function readChiefPointer(): Promise<string | null> {
  const { conversation_id } = await fetchJson<Pick<ChiefManagementSettings, 'conversation_id'>>(
    '/api/chief/conversation', { signal: AbortSignal.timeout(25_000) }
  );
  return conversation_id;
}
