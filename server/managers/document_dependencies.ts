/** Goal-unit composition seams. These calls stay open until Units 12/13 provide them. */
import type { ReadSql } from '../core/database/contracts.ts';
import type { ManagerPageDocument as PageDocument } from './contracts.ts';
export interface ManagerDocumentDependencies {
  /** Python list_goals(holder_id) followed by goal_json at the same snapshot/date. */
  manager_goals(sql: ReadSql, manager_id: string, now: Date): Record<string, unknown>[];
  /** Python prepare_document for charts, including category/formula validation. */
  prepare_rich_document(document: unknown): PageDocument;
  /** Python validate_page_bindings: measures, formula graph, datasets, manual origin. */
  validate_page_bindings(sql: ReadSql, document: PageDocument): void;
  /** Python document_readings_json for a PageReadingSubject with this id/revision. */
  document_readings(
    sql: ReadSql,
    subject: { id: string; page: PageDocument; revision: number },
    now: Date,
  ): Record<string, unknown>;
}
let dependencies: ManagerDocumentDependencies | undefined;
export function configure_manager_documents(value: ManagerDocumentDependencies): void {
  dependencies = value;
}
export function manager_document_dependencies(): ManagerDocumentDependencies {
  if (!dependencies)
    throw new Error('Manager goal, measure, formula, and dataset dependencies are not composed');
  return dependencies;
}
export function has_manager_document_dependencies(): boolean {
  return dependencies !== undefined;
}
