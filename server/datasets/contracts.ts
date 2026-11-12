import type {
  DatasetDimension,
  DatasetField,
  DatasetRow,
} from '../../web/src/lib/goals/contracts.ts';
export type {
  DatasetDimension,
  DatasetField,
  DatasetRow,
  DatasetProvenance,
  DatasetResult,
  OutputDataset,
} from '../../web/src/lib/goals/contracts.ts';
export type DatasetFieldKind = DatasetField['kind'];
export interface Dataset {
  id: string;
  project_id: string;
  source_id: string;
  name: string;
  timezone: string;
  dimensions: DatasetDimension[];
  fields: DatasetField[];
  definition_revision: number;
  created_at: number;
  updated_at: number;
}
export type CreateDatasetBody = Pick<
  Dataset,
  'project_id' | 'source_id' | 'name' | 'timezone' | 'dimensions' | 'fields'
>;
export type UpdateDatasetBody = Partial<
  Pick<Dataset, 'name' | 'timezone' | 'dimensions' | 'fields'>
> & { expected_revision: number };
export interface CandidateDatasetResult {
  dataset_id: string;
  date: string;
  rows: DatasetRow[];
  query_end_at: number;
  query_start_at: number | null;
  labels: Record<string, Record<string, string>>;
  unavailable_reason: string | null;
  limitation: string | null;
}
export interface ReadDatasetsBody {
  dataset_ids: string[];
  first_date: string;
  last_date: string;
  limit?: number;
}
