import type { StopIntent } from '../contracts.ts';
export function consume_stop_intent(
  intent: StopIntent | null,
  incarnation: string,
  now: number,
): boolean {
  return intent !== null && intent.incarnation === incarnation && now < intent.expires_at;
}
