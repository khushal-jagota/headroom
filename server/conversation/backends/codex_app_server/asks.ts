import { python_string_repr as shared_string_repr } from '../../../core/python_repr.ts';
/** Permission and question conversion without wire or record ownership. */
import { PermissionAskOption, UserInputOption, UserInputQuestion } from '../../events.ts';
import type { UserInputAnswer } from '../../events.ts';
import * as P from './protocol.ts';
import { shortened } from './events.ts';
import { TOOL_CALL_TITLE_MAXIMUM_CHARACTERS } from './adapter_configuration.ts';
export const PERMISSION_ASK_OPTIONS = [
  new PermissionAskOption({ option_id: 'accept', label: 'Approve', option_kind: 'allow_once' }),
  new PermissionAskOption({
    option_id: 'acceptForSession',
    label: 'Approve for the rest of this session',
    option_kind: 'allow_always',
  }),
  new PermissionAskOption({ option_id: 'decline', label: 'Decline', option_kind: 'reject_once' }),
];
export function permission_ask(
  method: string,
  params: P.HandledServerRequest,
): { turn_id: string; title: string; detail: string | null } | null {
  if (method === 'item/commandExecution/requestApproval') {
    const given = params as P.CommandExecutionRequestApprovalParams;
    return {
      turn_id: given.turnId,
      title: `Run ${shortened(given.command || given.itemId, TOOL_CALL_TITLE_MAXIMUM_CHARACTERS)}`,
      detail: given.reason || given.cwd,
    };
  }
  if (method === 'item/fileChange/requestApproval') {
    const given = params as P.FileChangeRequestApprovalParams;
    return {
      turn_id: given.turnId,
      title: 'Change files',
      detail: given.reason || given.grantRoot,
    };
  }
  return null;
}
export function approval_answer(method: string, decision: string): Record<string, unknown> {
  return (
    method === 'item/fileChange/requestApproval'
      ? P.FileChangeRequestApprovalResponse
      : P.CommandExecutionRequestApprovalResponse
  ).read({ decision });
}
export function user_input_questions(
  questions: readonly P.ToolRequestUserInputQuestion[],
): readonly UserInputQuestion[] {
  if (!questions.length) throw new Error('Codex requested user input without any questions');
  const translated: UserInputQuestion[] = [];
  const ids = new Set<string>();
  for (const question of questions) {
    const id = question.id.trim();
    const header = question.header.trim();
    const prompt = question.question.trim();
    if (!id || !header || !prompt)
      throw new Error('Codex requested user input with a blank id, header, or question');
    if (ids.has(id)) throw new Error(`Codex repeated user-input question id ${repr(id)}`);
    if (question.isSecret)
      throw new Error(`Codex question ${repr(id)} asks for unsupported secret input`);
    const options = (question.options ?? []).map(
      (option) =>
        new UserInputOption({ label: option.label.trim(), description: option.description.trim() }),
    );
    if (options.some((option) => !option.label || !option.description))
      throw new Error(`Codex question ${repr(id)} has an option without a label or description`);
    ids.add(id);
    translated.push(
      new UserInputQuestion({
        question_id: id,
        header,
        question: prompt,
        options,
        multi_select: false,
        allow_other: Boolean(question.isOther) || !options.length,
      }),
    );
  }
  return translated;
}
export function user_input_answer(answers: readonly UserInputAnswer[]): Record<string, unknown> {
  return P.ToolRequestUserInputResponse.read({
    answers: Object.fromEntries(
      answers.map((answer) => [answer.question_id, { answers: [...answer.answers] }]),
    ),
  });
}
export const repr = shared_string_repr;
