/**
 * Each driver's transcripts on this machine, for the recorded usage service. They come from the
 * drivers, as T3 Code's built-in usage drivers do: no live instance runs for them. The server
 * and every joined machine read their own transcripts this way.
 */
import type { AnyAgentDriver } from '../../drivers/core/driver.ts';
import { default_instance_configs } from '../../drivers/core/registry.ts';
import type { UsageTranscriptSource } from './contracts.ts';

export async function driver_transcript_sources(
  drivers: readonly AnyAgentDriver[],
  environment: Readonly<NodeJS.ProcessEnv>,
): Promise<UsageTranscriptSource[]> {
  const sources = await Promise.all(
    [...default_instance_configs(drivers)].map(async ([instance_id, config]) => {
      const driver = drivers.find((candidate) => candidate.driver_kind === config.driver);
      return driver?.transcript_usage === undefined
        ? []
        : [{ provider: instance_id, source: await driver.transcript_usage(environment) }];
    }),
  );
  return sources.flat();
}
