/** Export-only proof for configuration intake and canonical sidecar placement. */
import { load_metrics_config, store_path } from '../configuration.ts';
export async function metrics_config_parity(input: {
  environments: Record<string, string>[];
  paths: string[];
}): Promise<unknown> {
  const configurations = input.environments.map((environment) => {
    try {
      return { value: load_metrics_config(environment) };
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      return { error: { name: error.name, message: error.message } };
    }
  });
  return {
    configurations,
    paths: await Promise.all(input.paths.map(store_path)),
  };
}
