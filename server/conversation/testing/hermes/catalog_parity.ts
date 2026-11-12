/** Synthetic inventory commands only. The retained Hermes script is never executed. */
import {
  HermesModelCatalogOwner,
  hermes_model_catalog_launch,
} from '../../backends/hermes_acp/catalog.ts';
import { MODEL_CATALOG_PROBE_TIMEOUT_SECONDS } from '../../snapshot/configuration.ts';
interface Specification {
  launch: Parameters<typeof hermes_model_catalog_launch>[0];
  command: string[];
  cases: { name: string; timeout_seconds: number; refresh: boolean }[];
}
export async function run_catalog_parity(spec: Specification): Promise<unknown> {
  const before = { ...process.env };
  const launch = hermes_model_catalog_launch(spec.launch);
  const launches = [false, true].map((refresh) => ({
    argv: [...launch.argv, ...(refresh ? ['--refresh'] : [])],
    timeout_seconds: MODEL_CATALOG_PROBE_TIMEOUT_SECONDS,
    environment_overrides: Object.fromEntries(launch.environment_overrides),
  }));
  const outcomes = [];
  const case_seconds: Record<string, number> = {};
  for (const item of spec.cases) {
    const owner = new HermesModelCatalogOwner(
      {
        argv: item.name === 'missing' ? [spec.launch.hermes_python] : [...spec.command, item.name],
        environment_overrides: launch.environment_overrides,
      },
      { timeout_seconds: item.timeout_seconds },
    );
    const started = performance.now();
    try {
      outcomes.push({ name: item.name, outcome: await owner.source({ refresh: item.refresh }) });
    } finally {
      await owner.shutdown();
      case_seconds[item.name] = (performance.now() - started) / 1000;
    }
  }
  return {
    launches,
    outcomes,
    case_seconds,
    parent_environment_unchanged: JSON.stringify(process.env) === JSON.stringify(before),
  };
}
