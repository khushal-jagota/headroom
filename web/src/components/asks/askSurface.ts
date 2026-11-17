import type {
  AskDraft,
  PreviewAsk,
  TranscriptLine
} from "../../lib/asks/preview";

export type AskHandlers = {
  onCommitChoice: (index: number, choiceId: string) => void;
  onFinish: (index: number, sentence: string) => void;
  onToggle: (index: number, choiceId: string) => void;
  onOther: (index: number, value: string) => void;
  onText: (index: number, value: string) => void;
  onCommit: (index: number) => void;
  onCancel: (index: number) => void;
  onStep: (index: number, delta: number) => void;
  onActivate: (index: number) => void;
  onKey: (event: KeyboardEvent, index: number) => void;
};

export type AskSurface = {
  lines: readonly TranscriptLine[];
  asks: readonly PreviewAsk[];
  drafts: readonly AskDraft[];
  cursors: readonly number[];
  active: number;
  handlers: AskHandlers;
};
