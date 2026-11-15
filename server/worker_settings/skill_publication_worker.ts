/** Blocking filesystem publication settles before normal server work starts. */
import { parentPort, workerData } from 'node:worker_threads';
import { provision_workspace_skills } from '../skills/sources.ts';
import { provision_account_skills } from '../skills/account.ts';
export interface SkillPublicationInput {
  parent: string;
  workspace: string | null;
  account_homes: string[] | null;
}
if (!parentPort) throw new Error('Skill publication requires a worker thread');
const input = workerData as SkillPublicationInput;
try {
  if (input.workspace !== null) provision_workspace_skills(input.workspace, input.parent);
  parentPort.postMessage({
    warning:
      input.account_homes === null
        ? null
        : provision_account_skills(input.parent, input.account_homes),
  });
} catch (error) {
  parentPort.postMessage({ error: error instanceof Error ? error.message : String(error) });
}
parentPort.close();
