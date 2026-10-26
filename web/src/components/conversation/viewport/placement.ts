/** A placement belongs to one reader and survives asynchronous presentation. */
export type ThreadPlacement = {
  reader: object | null;
  destination: 'entry' | 'sent';
  messageId: string | null;
  cancelled: boolean;
};
export function threadPlacement(reader: object | null, destination: ThreadPlacement['destination'], messageId: string | null = null): ThreadPlacement {
  return { reader, destination, messageId, cancelled: false };
}
export function currentPlacement(operation: ThreadPlacement | null, reader: object | null): boolean {
  return operation !== null && !operation.cancelled && operation.reader === reader;
}
/** Parsed data does not establish layout. Only admitted, active presentation blocks gate placement. */
export function presentationReady(element: Element): boolean {
  return !element.matches('[aria-busy="true"], [data-markdown-rendering]') &&
    element.querySelector('[aria-busy="true"], [data-markdown-rendering]') === null;
}
/** Only a text write observed at newest can pay for line-at-a-time travel. */
export function streamArrivalBudget(textChanged: boolean, wasFollowing: boolean, before: number, after: number): number {
  return textChanged && wasFollowing ? Math.max(0, after - before) : 0;
}
/** Layout or destination distance cannot be charged to an arriving text write. */
export function streamTravelFits(distance: number, remainingArrivalPixels: number): boolean {
  return distance >= 0.5 && remainingArrivalPixels >= 0.5 && distance <= remainingArrivalPixels + 1;
}

import type { ViewReaderAnchor } from '../../../lib/conversation/view/anchorContracts';
import type { MarkdownTargetDemand } from '../../../lib/markdown/presentationContracts';
export type TextRestorationPlacement = {
  readonly reader: object | null;
  readonly anchor: ViewReaderAnchor;
  readonly attempt: number;
  demand: MarkdownTargetDemand | null;
  unsubscribe: (() => void) | null;
};
export function releaseTextRestoration(operation: TextRestorationPlacement | null): void {
  operation?.unsubscribe?.();
  operation?.demand?.release();
  if (operation) { operation.unsubscribe = null; operation.demand = null; }
}
