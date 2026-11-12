/** Shared values required by the backend seam. Event persistence belongs to the core. */
export const ConversationTurnEnding = {
  completed: 'completed',
  failed: 'failed',
  interrupted: 'interrupted',
} as const;
export type ConversationTurnEnding =
  (typeof ConversationTurnEnding)[keyof typeof ConversationTurnEnding];
export const AutomaticCompactionResult = { not_compacted: 'not_compacted' } as const;
export type AutomaticCompactionResult =
  (typeof AutomaticCompactionResult)[keyof typeof AutomaticCompactionResult];
export const ToolCallStatus = { completed: 'completed', failed: 'failed' } as const;
export type ToolCallStatus = (typeof ToolCallStatus)[keyof typeof ToolCallStatus];
export const PlanEntryStatus = {
  pending: 'pending',
  in_progress: 'in_progress',
  completed: 'completed',
} as const;
export type PlanEntryStatus = (typeof PlanEntryStatus)[keyof typeof PlanEntryStatus];
export class PlanEntry {
  readonly text: string;
  readonly status: PlanEntryStatus;
  constructor(values: { text: string; status: PlanEntryStatus }) {
    this.text = values.text;
    this.status = values.status;
  }
}
export class PermissionAskOption {
  readonly option_id: string;
  readonly label: string;
  readonly option_kind: string;
  constructor(values: { option_id: string; label: string; option_kind: string }) {
    this.option_id = values.option_id;
    this.label = values.label;
    this.option_kind = values.option_kind;
  }
}
export class UserInputOption {
  readonly label: string;
  readonly description: string;
  constructor(values: { label: string; description: string }) {
    this.label = values.label;
    this.description = values.description;
  }
}
export class UserInputQuestion {
  readonly question_id: string;
  readonly header: string;
  readonly question: string;
  readonly options: readonly UserInputOption[];
  readonly multi_select: boolean;
  readonly allow_other: boolean;
  constructor(values: {
    question_id: string;
    header: string;
    question: string;
    options: readonly UserInputOption[];
    multi_select: boolean;
    allow_other: boolean;
  }) {
    this.question_id = values.question_id;
    this.header = values.header;
    this.question = values.question;
    this.options = values.options;
    this.multi_select = values.multi_select;
    this.allow_other = values.allow_other;
  }
}
export class UserInputAnswer {
  readonly question_id: string;
  readonly answers: readonly string[];
  constructor(values: { question_id: string; answers: readonly string[] }) {
    this.question_id = values.question_id;
    this.answers = values.answers;
  }
}
