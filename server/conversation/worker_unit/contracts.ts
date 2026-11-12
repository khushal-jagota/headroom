export class WorkerUnitError extends Error {}

export interface WorkerUnitValues {
  name: string;
  directory: string;
  environment: Record<string, string>;
  working_directory: string;
  slice_name: string;
}
