/** Compose Manager and Chief documents with the canonical Goal reading engine. */
import { PlannerError } from '../core/contracts.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { newId } from '../core/ids.ts';
import { configure_manager_documents } from '../managers/document_dependencies.ts';
import { read_dataset } from '../datasets/data/index.ts';
import { read_definitions, read_measure } from '../measures/data.ts';
import { validate_graph } from '../measures/logic/formulas.ts';
import type { PageDocument } from './contracts.ts';
import { list_goals } from './data.ts';
import { goal_json, document_readings_json } from './views.ts';
import { with_calendar_iso } from './calendar.ts';
import {
  prepare_document,
  charts,
  measure_ids,
  dataset_ids,
  expectation_measure_ids,
} from './logic/documents.ts';
import { validate_category_references } from './logic/categories.ts';
export function validate_page_bindings(sql: ReadSql, page: PageDocument): void {
  try {
    const identifiers = [
      ...new Set([...measure_ids(null, page), ...expectation_measure_ids(null, page)]),
    ];
    const definitions = read_definitions(sql, identifiers);
    const datasets = new Map(dataset_ids(null, page).map((id) => [id, read_dataset(sql, id)]));
    for (const id of identifiers) validate_graph(id, definitions);
    for (const chart of charts(null, page)) {
      if ('binding' in chart) validate_category_references(chart, datasets, definitions);
      if ('manual' in chart && read_measure(sql, chart.measure_id!).origin !== 'manual')
        throw new TypeError('manual controls require a manual-origin measure');
    }
  } catch (error) {
    if (error instanceof TypeError) throw new PlannerError('validation', error.message);
    throw error;
  }
}
export function configure_goal_manager_documents(): void {
  configure_manager_documents({
    manager_goals: (sql, id, now, calendar_iso) =>
      list_goals(sql, { holder_id: id }).map((goal) => ({
        ...goal_json(sql, goal, with_calendar_iso(now, calendar_iso)),
      })),
    prepare_rich_document: (document) => prepare_document(document, () => newId('block')),
    validate_page_bindings,
    document_readings: (sql, subject, now, calendar_iso) =>
      document_readings_json(sql, subject, now, { calendar_iso }),
  });
}
