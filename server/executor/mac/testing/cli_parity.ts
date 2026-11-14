/** Export-only CLI companion. The Python driver owns its scripted Unix listener. */
import { readFileSync } from 'node:fs';
import { run } from '../../../cli/main.ts';
import { request } from '../ipc_client.ts';
const input = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const started = performance.now();
const results = [];
for (const scenario of input.scenarios) {
  const environment = {
    ...input.environment,
    PLAN_EXECUTOR_ROOT:
      input.environment.PLAN_EXECUTOR_ROOT + (scenario.mode === 'missing' ? '/absent' : ''),
  };
  if (scenario.direct) {
    try {
      results.push(
        await request(scenario.operation, scenario.payload, environment, {
          result_seconds: 0.03,
          reserve_seconds: 0.02,
        }),
      );
    } catch (error) {
      results.push({ error: (error as Error).message });
    }
  } else
    results.push(
      await run(scenario.argv, environment, async () => Buffer.from(scenario.stdin ?? '')),
    );
}
process.stdout.write(
  JSON.stringify({ results, elapsed_seconds: (performance.now() - started) / 1000 }),
);
