/** Goal and dataset services for Manager pages. */
import type { ReadSql } from '../core/database/contracts.ts';
import type { ManagerPageDocument as PageDocument } from './contracts.ts';
export interface ManagerDocumentDependencies {
  /** Read and present the holder's goals at one snapshot and date. */
  manager_goals(
    sql: ReadSql,
    manager_id: string,
    now: Date,
    calendar_iso?: string,
  ): Record<string, unknown>[];
  /** Prepare charts, including category and formula validation. */
  prepare_rich_document(document: unknown): PageDocument;
  /** Validate measure, formula graph, dataset, and manual-origin bindings. */
  validate_page_bindings(sql: ReadSql, document: PageDocument): void;
  /** Project readings for the page's identity and revision. */
  document_readings(
    sql: ReadSql,
    subject: { id: string; page: PageDocument; revision: number },
    now: Date,
    calendar_iso?: string,
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
