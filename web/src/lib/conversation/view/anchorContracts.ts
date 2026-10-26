import type { MarkdownTextLocation } from '../../markdown/presentationContracts';
export type ConversationContentLocation = Readonly<{
  pieceIndex: number;
  text: MarkdownTextLocation;
  lineOffset: number;
}>;
export type ViewReaderAnchor = Readonly<{
  key: string;
  order: Readonly<{ sequence: number; slot: number }>;
  offset: number;
  following: boolean;
  content?: ConversationContentLocation;
}>;
export type RestorationOutcome = 'idle' | 'pending' | 'failed' | 'unavailable';
