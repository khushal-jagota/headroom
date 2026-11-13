import type { CanUseTool, PermissionResult } from '@anthropic-ai/claude-agent-sdk';
import { AnswerFate } from '../../contracts.ts';
import { PermissionAskOption, UserInputOption, UserInputQuestion } from '../../events.ts';
import type { UserInputAnswer } from '../../events.ts';
import * as B from '../contracts.ts';
import { Pending } from './turn.ts';
import type { TurnInFlight, UserQuestion, ParkedPermission, ParkedUserInput } from './turn.ts';
import { canonical_json, record } from './events.ts';
import { error_text } from './failure_summary.ts';

const PERMISSION_OPTIONS = [
  new PermissionAskOption({
    option_id: 'approve_once',
    label: 'Approve once',
    option_kind: 'allow_once',
  }),
  new PermissionAskOption({
    option_id: 'always_allow_this_session',
    label: 'Always allow this session',
    option_kind: 'allow_always',
  }),
  new PermissionAskOption({ option_id: 'decline', label: 'Decline', option_kind: 'reject_once' }),
];
export function user_questions(input: Record<string, unknown>): UserQuestion[] | null {
  if (!Array.isArray(input.questions) || !input.questions.length) return null;
  const parsed: UserQuestion[] = [];
  const seen = new Set<string>();
  for (const value of input.questions) {
    const asked = record(value);
    const text = asked?.question;
    if (!asked || typeof text !== 'string' || !text || seen.has(text)) return null;
    seen.add(text);
    if (!Array.isArray(asked.options) || !asked.options.length) return null;
    const choices: UserQuestion['choices'] = [];
    for (const option of asked.options) {
      const fields = record(option);
      if (
        !fields ||
        typeof fields.label !== 'string' ||
        !fields.label ||
        typeof fields.description !== 'string'
      )
        return null;
      choices.push({ label: fields.label, description: fields.description });
    }
    if (
      new Set(choices.map((choice) => choice.label)).size !== choices.length ||
      typeof asked.multiSelect !== 'boolean' ||
      typeof asked.header !== 'string'
    )
      return null;
    parsed.push({ text, header: asked.header, choices, multi_select: asked.multiSelect });
  }
  return parsed;
}
export class ClaudeAsks {
  private count = 0;
  readonly sink: B.BackendEventSink;
  readonly current_turn: () => TurnInFlight | null;
  constructor(sink: B.BackendEventSink, current_turn: () => TurnInFlight | null) {
    this.sink = sink;
    this.current_turn = current_turn;
  }
  readonly can_use_tool: CanUseTool = async (tool_name, tool_input, context) => {
    const turn = this.current_turn();
    if (!turn) return { behavior: 'deny', message: 'The turn ended before this was answered.' };
    this.count += 1;
    const ask_id = `${context.toolUseID || tool_name}:${this.count}`;
    let parked: ParkedPermission | ParkedUserInput;
    if (tool_name === 'AskUserQuestion') {
      const questions = user_questions(tool_input);
      if (!questions) {
        await this.sink.report(
          turn.token,
          new B.UserInputFailed({
            request_id: ask_id,
            detail: 'Claude sent a malformed question request, so Panels refused it.',
          }),
        );
        return {
          behavior: 'deny',
          message: 'The question request was malformed and could not be shown.',
        };
      }
      const input: ParkedUserInput = {
        answer: new Pending(),
        handed_over: new Pending(),
        tool_input: { ...tool_input },
        questions,
      };
      parked = input;
      turn.parked_user_inputs.set(ask_id, input);
      if (context.toolUseID != null) turn.user_input_tool_use_ids.add(context.toolUseID);
      await this.sink.report(
        turn.token,
        new B.UserInputRequested({
          request: new B.BackendUserInputRequest({
            request_id: ask_id,
            questions: questions.map(
              (question) =>
                new UserInputQuestion({
                  question_id: question.text,
                  header: question.header,
                  question: question.text,
                  options: question.choices.map((choice) => new UserInputOption(choice)),
                  multi_select: question.multi_select,
                  allow_other: true,
                }),
            ),
          }),
        }),
      );
    } else {
      const permission: ParkedPermission = {
        answer: new Pending(),
        handed_over: new Pending(),
        tool_input: { ...tool_input },
        suggestions: context.suggestions ?? [],
      };
      parked = permission;
      turn.parked_asks.set(ask_id, permission);
      await this.sink.report(
        turn.token,
        new B.PermissionAskRaised({
          ask: new B.BackendPermissionAsk({
            ask_id,
            title: tool_name,
            detail: canonical_json(tool_input),
            options: PERMISSION_OPTIONS,
          }),
        }),
      );
    }
    const abort = () =>
      parked.answer.reject(context.signal.reason ?? new Error('The operation was aborted'));
    context.signal.addEventListener('abort', abort, { once: true });
    if (context.signal.aborted) abort();
    try {
      const answer = await parked.answer.promise;
      parked.handed_over.resolve();
      return answer;
    } catch (error) {
      const failed =
        tool_name === 'AskUserQuestion'
          ? new B.UserInputAnswerWriteFailed(error_text(error))
          : new B.PermissionAnswerWriteFailed(error_text(error));
      parked.handed_over.reject(failed);
      throw error;
    } finally {
      context.signal.removeEventListener('abort', abort);
    }
  };
  async answer_permission(ask_id: string, option_id: string): Promise<AnswerFate> {
    const turn = this.current_turn();
    const parked = turn?.parked_asks.get(ask_id);
    if (!parked || parked.answer.settled) throw new B.PermissionAnswerWriteFailed(ask_id);
    let answer: PermissionResult;
    if (option_id === 'approve_once')
      answer = { behavior: 'allow', updatedInput: { ...parked.tool_input } };
    else if (option_id === 'always_allow_this_session')
      answer = {
        behavior: 'allow',
        updatedInput: { ...parked.tool_input },
        ...(parked.suggestions.length ? { updatedPermissions: [...parked.suggestions] } : {}),
      };
    else if (option_id === 'decline')
      answer = { behavior: 'deny', message: 'User declined tool execution.' };
    else
      throw new B.PermissionAnswerWriteFailed(
        `${ask_id} was not offered ${JSON.stringify(option_id)}`,
      );
    turn!.parked_asks.delete(ask_id);
    parked.answer.resolve(answer);
    try {
      await parked.handed_over.promise;
    } catch {
      throw new B.PermissionAnswerWriteFailed(ask_id);
    }
    return AnswerFate.applied;
  }
  async answer_user_input(
    request_id: string,
    answers: readonly UserInputAnswer[],
  ): Promise<AnswerFate> {
    const turn = this.current_turn();
    const parked = turn?.parked_user_inputs.get(request_id);
    if (!parked || parked.answer.settled) throw new B.UserInputAnswerWriteFailed(request_id);
    const answer_map: Record<string, string> = {};
    const invalid =
      answers.length !== parked.questions.length ||
      parked.questions.some((question, index) => {
        const answer = answers[index];
        if (
          !answer ||
          answer.question_id !== question.text ||
          !answer.answers.length ||
          (!question.multi_select && answer.answers.length !== 1) ||
          answer.answers.some((value) => !value)
        )
          return true;
        Object.defineProperty(answer_map, question.text, {
          value: answer.answers.join(', '),
          enumerable: true,
          writable: true,
          configurable: true,
        });
        return false;
      });
    if (invalid)
      throw new B.UserInputAnswerWriteFailed(`${request_id} did not receive complete answers`);
    turn!.parked_user_inputs.delete(request_id);
    parked.answer.resolve({
      behavior: 'allow',
      updatedInput: { ...parked.tool_input, answers: answer_map },
    });
    try {
      await parked.handed_over.promise;
    } catch {
      throw new B.UserInputAnswerWriteFailed(request_id);
    }
    return AnswerFate.applied;
  }
}
