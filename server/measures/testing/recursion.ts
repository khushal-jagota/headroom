/** Exercise the fixed TS policy; the Python driver gates only interior Python agreement. */
import { readFileSync } from 'node:fs';
import { createDatabase, createReadAccess } from '../../core/database/index.ts';
import { PlannerError } from '../../core/contracts.ts';
import { stringify_numeric_response } from '../../core/http/numeric_intake.ts';
import { create_measure, read_values } from '../actions.ts';
import { read_definitions, read_demanded } from '../data.ts';
import { ArithmeticDepthLimit, demanded_pairs, Evaluation } from '../logic/evaluation.ts';
interface Spec {
  db_path: string;
  now: number;
  day: string;
  roots: { root: string; independent: string }[];
  mode?: 'demand' | 'admission';
  demand_roots?: string[];
  body?: Parameters<typeof create_measure>[1];
}
const project = (value: { status: string; value: number | null }) => ({
  status: value.status,
  value: value.value,
});
export function run(spec: Spec) {
  const access = createReadAccess(spec.db_path);
  try {
    return access.read((sql) =>
      spec.roots.map(({ root, independent }) => {
        const roots = [root, independent].map((measure_id) => ({ measure_id, date: spec.day }));
        const definitions = read_definitions(sql, [root, independent]);
        const pairs = demanded_pairs(roots, definitions, spec.now);
        const evaluation = new Evaluation(
          definitions,
          read_demanded(sql, pairs, definitions),
          spec.now,
        );
        return {
          measures: read_values(
            sql,
            {
              measure_ids: [root, independent],
              first_date: spec.day,
              last_date: spec.day,
            },
            spec.now,
          ).values.map(project),
          goal_actuals: roots.map((pair) => project(evaluation.measure(pair))),
        };
      }),
    );
  } finally {
    access.close();
  }
}
export function demand_run(spec: Spec) {
  const access = createReadAccess(spec.db_path);
  try {
    return access.read((sql) =>
      spec.demand_roots!.map((root) => {
        const identifiers = [root, 'independent'];
        const definitions = read_definitions(sql, identifiers);
        try {
          const count = demanded_pairs(
            identifiers.map((measure_id) => ({ measure_id, date: spec.day })),
            definitions,
            spec.now,
          ).size;
          return { root, status: 'value', count };
        } catch (error) {
          if (!(error instanceof ArithmeticDepthLimit)) throw error;
          return { root, status: 'arithmetic_limit', count: null };
        }
      }),
    );
  } finally {
    access.close();
  }
}
export function admission_run(spec: Spec) {
  const database = createDatabase(spec.db_path);
  try {
    let status = 200;
    let body: unknown = { status: 'accepted' };
    try {
      const result = database.write((sql) => create_measure(sql, spec.body!, spec.now));
      stringify_numeric_response(result);
    } catch (error) {
      if (error instanceof PlannerError) {
        status = error.statusCode;
        body = error.toPayload();
      } else {
        status = 500;
        body = 'Internal Server Error';
      }
    }
    const rows = database.read((sql) =>
      sql.all<Record<string, unknown>>(
        `SELECT project_id,name,unit,origin,kind,timezone,period,source_id,formula,
      definition_revision,write_revision,created_at,updated_at
      FROM measures WHERE name='Admission proof'`,
      ),
    );
    const dependencies = database.read((sql) =>
      sql.all<{ input_measure_id: string }>(
        `SELECT input_measure_id FROM measure_dependencies d JOIN measures m ON m.id=d.measure_id
      WHERE m.name='Admission proof' ORDER BY input_measure_id`,
      ),
    );
    return { status, body, rows, dependencies };
  } finally {
    database.close();
  }
}
if (process.argv[1]?.endsWith('/recursion.ts')) {
  const spec = JSON.parse(readFileSync(0, 'utf8')) as Spec;
  process.stdout.write(
    JSON.stringify(
      spec.mode === 'demand'
        ? demand_run(spec)
        : spec.mode === 'admission'
          ? admission_run(spec)
          : run(spec),
    ),
  );
}
