import type { ChecklistBlock, Goal, PageDocument } from '../contracts.ts';
import { blocks } from './documents.ts';
export function selected_checklist(page: PageDocument, id: string | null): ChecklistBlock | null {
  if (id === null) return null;
  return (
    (blocks(page).find((block) => block.id === id && block.type === 'checklist') as
      ChecklistBlock | undefined) ?? null
  );
}
export function checklist_completed(checklist: ChecklistBlock): boolean {
  return checklist.items.length > 0 && checklist.items.every((item) => item.state === 'done');
}
export function effective_completion(
  binary_checked: boolean,
  page: PageDocument,
  id: string | null,
): boolean {
  if (id === null) return binary_checked;
  const checklist = selected_checklist(page, id);
  return checklist !== null && checklist_completed(checklist);
}
export function goal_completed(goal: Goal): boolean {
  if (goal.kind === 'quantitative' && goal.completion_rule !== null)
    return goal.completion_date !== null;
  return effective_completion(goal.binary_checked, goal.page, goal.primary_checklist_block_id);
}
export function completion_date_after_edit(
  current: Goal,
  accepted: Goal,
  today: string,
  creating: boolean,
): string | null {
  if (!goal_completed(accepted)) return null;
  if (creating || !goal_completed(current)) return today;
  return current.completion_date;
}
export function prepare_completion(
  current: Goal,
  resulting: Goal,
  options: {
    creating: boolean;
    selection_explicit: boolean;
    chart_explicit: boolean;
    completed: boolean | null;
  },
): Goal {
  const { creating, selection_explicit, chart_explicit, completed } = options,
    guidance = `inspect panels goal schema ${creating ? 'create' : 'edit'} --json`,
    changed = current.kind !== resulting.kind;
  let selector = resulting.primary_checklist_block_id;
  if (selector !== null && typeof selector !== 'string')
    throw new Error(`primary_checklist_block_id must be text or null; ${guidance}`);
  const checklist = selected_checklist(resulting.page, selector);
  if (selection_explicit && selector !== null) {
    if (resulting.kind !== 'binary')
      throw new Error(`only binary goals can select a primary checklist; ${guidance}`);
    if (checklist === null)
      throw new Error(
        `primary_checklist_block_id must identify a saved checklist; use null to select binary completion; ${guidance}`,
      );
  }
  if (resulting.kind === 'binary') {
    if ((creating || changed) && !selection_explicit)
      throw new Error(
        `primary_checklist_block_id is required: use null for binary completion or a saved checklist ID; ${guidance}`,
      );
    if (
      !selection_explicit &&
      checklist === null &&
      selected_checklist(current.page, current.primary_checklist_block_id) !== null
    )
      throw new Error(
        `the selected checklist was removed: explicitly set primary_checklist_block_id to a replacement saved checklist ID or null for binary completion; ${guidance}`,
      );
  } else {
    if ((creating || changed) && !chart_explicit)
      throw new Error(
        `primary_chart is required for a quantitative display: supply a chart object with an explicit kind; ${guidance}`,
      );
    if (chart_explicit && resulting.primary_chart === null)
      throw new Error(
        `primary_chart cannot be null for a quantitative display: supply a chart object or explicitly switch kind to binary and set primary_checklist_block_id to a saved checklist ID or null; ${guidance}`,
      );
    if (creating || changed) selector = null;
  }
  let checked = resulting.binary_checked;
  if (current.completion_rule !== null && resulting.kind === 'binary')
    checked = goal_completed(current);
  if (current.primary_checklist_block_id !== null && selector === null)
    checked = effective_completion(
      current.binary_checked,
      current.page,
      current.primary_checklist_block_id,
    );
  if (completed !== null) {
    if (selector === null) checked = completed;
    else if (completed !== effective_completion(checked, resulting.page, selector))
      throw new Error("completion follows the selected checklist's items");
  }
  return { ...resulting, binary_checked: checked, primary_checklist_block_id: selector };
}
