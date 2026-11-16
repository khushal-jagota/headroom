import { expect, it } from 'vitest';
import { agent_cgroup } from './logic.ts';

it('recognizes installation service and Worker slice without classifying owner shells', () => {
  const units = ['panels-ts-trial.service', 'panels-trial-workers.slice'];
  expect(agent_cgroup('0::/user.slice/panels-ts-trial.service/child', units)).toBe(true);
  expect(agent_cgroup('0::/user.slice/panels-trial-workers.slice/worker.service', units)).toBe(
    true,
  );
  expect(agent_cgroup('0::/user.slice/user@1000.service/app.slice/terminal.scope', units)).toBe(
    false,
  );
  expect(agent_cgroup('0::/user.slice/other-panels-ts-trial.service', units)).toBe(false);
});
