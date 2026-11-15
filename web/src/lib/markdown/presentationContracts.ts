/** Browser-local text addresses. These types never cross the conversation wire. */
export type MarkdownContentIdentity = Readonly<{
  algorithm: 'sha256';
  digest: string;
  utf16Length: number;
}>;
export type MarkdownTextLocation = Readonly<{
  contentIdentity: MarkdownContentIdentity;
  blockIndex: number;
  textLeafPath: readonly number[];
  utf16Offset: number;
}>;
export type MarkdownTargetState =
  | { kind: 'pending' }
  | { kind: 'ready'; rect: DOMRectReadOnly }
  | { kind: 'failed'; retryable: true }
  | { kind: 'unavailable'; reason: 'source-changed' | 'missing-piece' | 'missing-leaf' };
export interface MarkdownTargetDemand {
  read(): MarkdownTargetState;
  subscribe(changed: () => void): () => void;
  release(): void;
}
export interface MarkdownPresentation {
  capture(viewport: DOMRectReadOnly): { location: MarkdownTextLocation; rect: DOMRectReadOnly } | null;
  demand(location: MarkdownTextLocation): MarkdownTargetDemand;
  measure(location: MarkdownTextLocation): MarkdownTargetState;
}
/** A direct message piece explicitly registers its own surface. Previews do not. */
export type RegisterMarkdownPresentation = (host: HTMLElement, presentation: MarkdownPresentation) => () => void;
