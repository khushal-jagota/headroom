import type { PlannerEvent } from "./ws";

export function keysForEvent(
  event: PlannerEvent,
  options?: { todayId?: string | null; includeTodayAlias?: boolean }
): string[];

export function knownEntityPrefixes(): string[];
