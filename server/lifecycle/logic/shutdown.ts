import type { StopIntent } from '../contracts.ts';
export function stop_intent_is_current(
  intent: StopIntent | null,
  incarnation: string,
  now: number,
): boolean {
  return intent !== null && intent.incarnation === incarnation && now < intent.expires_at;
}
