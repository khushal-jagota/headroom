import type { SkeletonShape } from "./contracts";

// Geometry comes from the approved shapes, never from the arriving content count.
export const rowWidths = ["68%", "54%", "77%", "61%", "44%", "72%"];
export const rowHeight = 38;
export const documentBlockHeight = 112;
export const documentBlockStep = 136;
export const documentHeadHeight = 125;
export const conversationPattern = [
  { height: 64, user: false }, { height: 40, user: true },
  { height: 92, user: false }, { height: 34, user: true },
  { height: 72, user: false }
];
export const conversationGap = 24;
export const reservation: Record<SkeletonShape, number> = {
  rows: 24 * 2 + 35 + 4 + rowWidths.length * rowHeight + 4,
  document: 24 * 2 + documentHeadHeight + documentBlockStep * 2,
  block: documentBlockHeight,
  conversation: conversationPattern.reduce((height, turn) => height + turn.height + conversationGap, 0),
  inline: 24
};

// Status speaks halfway through the visual grace period.
export const announcementDelayRatio = 0.5;
