export interface DatasetDimension {
  name: string;
  label: string;
}
export type DatasetFieldKind = 'count' | 'amount' | 'snapshot';
export interface DatasetField {
  name: string;
  label: string;
  unit: string;
  kind: DatasetFieldKind;
}
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
export interface OutputDataset {
  dataset_id: string;
  definition_revision: number;
  timezone: string;
  dimensions: DatasetDimension[];
  fields: DatasetField[];
  required_date: string;
}
export interface DatasetRow {
  dimensions: Record<string, string>;
  values: Record<string, number>;
}
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
export interface DatasetProvenance {
  run_id: string;
  source_id: string;
  source_revision: number;
  script_revision: number;
  script_sha256: string;
  definition_revision: number;
  collected_at: number;
}
export interface DatasetResult {
  dataset_id: string;
  date: string;
  rows: DatasetRow[];
  query_end_at: number;
  query_start_at: number | null;
  unavailable_reason: string | null;
  limitation: string | null;
  provenance: DatasetProvenance;
  output: OutputDataset;
}
export interface ReadDatasetsBody {
  dataset_ids: string[];
  first_date: string;
  last_date: string;
  limit?: number;
}
