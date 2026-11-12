export const TICKET_RECAP_PREVIEW_CHARS = 160;
export function recap_preview(recap: string): string { const compact = recap.trim().replace(/\s+/gu, ' '); const points = Array.from(compact); return points.length <= TICKET_RECAP_PREVIEW_CHARS ? compact : points.slice(0, TICKET_RECAP_PREVIEW_CHARS - 1).join('').trimEnd() + '…'; }
