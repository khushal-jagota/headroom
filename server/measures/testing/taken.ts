/** Read Python-produced retained evidence through the actual TypeScript measure action. */
import { createReadAccess } from '../../core/database/index.ts';
import { read_values } from '../actions.ts';
interface Spec {
  db_path: string;
  now: number;
  requests: {
    measure_ids: string[];
    first_date: string;
    last_date: string;
    limit?: number;
  }[];
}
export function run(spec: Spec) {
  const access = createReadAccess(spec.db_path);
  try {
    return access.read((sql) => spec.requests.map((body) => read_values(sql, body, spec.now)));
  } finally {
    access.close();
  }
}
