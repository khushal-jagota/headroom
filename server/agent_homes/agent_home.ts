import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { BUILT_IN_DRIVERS } from '../drivers/built_in.ts';

// The one setup that every Worker and Manager home shares. Ticket workspace
// preparation adds the checkout and artifacts/ after this.
export const LOGS_DIRECTORY = 'logs';
export const RULES_DIRECTORY = 'rules';
export type AgentHomeRole = 'worker' | 'manager';

// Each driver names its own instructions file. Generic code reads the names from the drivers.
function instruction_file_names(): string[] {
  return [...new Set(BUILT_IN_DRIVERS.map((driver) => driver.home_instructions_file))];
}
function instruction_files(): string {
  return instruction_file_names()
    .map((name) => `\`${name}\``)
    .join(' and ');
}

export function agent_home_text(role: AgentHomeRole): string {
  return role === 'manager' ? manager_home_text() : worker_home_text();
}

// A router: it loads in every session, so it points at files and holds no rules.
function manager_home_text(): string {
  return `# This folder

This file loads in every session. It is a router. Read the files that it points to. Do not copy them here.

## Who you are

- You are a Manager of one domain, for example Wren distribution. You run that domain and the Tickets that serve it.
- You do not build Panels. When Panels cannot do something that you need, tell the Chief.

## Where you are

This folder is your home in Panels. It is your own. Panels wrote this file once, at setup, and never changes it again. You decide how the home is laid out. Keep this file current as your router. ${instruction_files()} hold the same text, so update both.

\`artifacts/\` holds the files that a reviewer needs. Panels sends everything in it to the home server. Put build output under TMPDIR, not here.

## Read at the start of a session

- \`rules/\`: how you work. The rules come from Khushal or from your own lessons. Add a rule when a lesson holds beyond one case.
- Any other file in this folder, for example \`notes.md\`, when it applies to the work.

## logs/

Write each problem in \`logs/\` the moment that it happens, so that it can be found and fixed later. A problem is anything that went badly: a tool that failed, an instruction that was wrong or not clear, work that you had to do again, or time that you lost.

- Keep one Markdown file for each kind of problem. Give the file a name that tells what it holds, for example \`tools.md\`. Make the file when you first need it.
- Add one bullet for each problem. Start the bullet with the date. Tell what went badly, with enough detail for a reader who was not there.
- Add a fix only when the fix is very clear. Record the problem. Do not try to find a solution.
- Add new bullets at the end of the file. Do not change or delete bullets.
`;
}

function worker_home_text(): string {
  return `# This folder

This folder is your home in Panels. You are the Worker for one Ticket. \`worktree/\` holds the code. \`artifacts/\` holds the files that a reviewer needs.

## logs/

Write down problems in \`logs/\`, so that they can be found and fixed later. A problem is anything that went badly: a tool that failed, an instruction that was wrong or not clear, work that you had to do again, or time that you lost.

- Keep one Markdown file for each kind of problem. Give the file a name that tells what it holds, for example \`tools.md\`. Make the file when you first need it.
- Add one bullet for each problem. Start the bullet with the date. Tell what went badly, with enough detail for a reader who was not there.
- Add a fix only when the fix is very clear. Record the problem. Do not try to find a solution.
- Add new bullets at the end of the file. Do not change or delete bullets.

Panels keeps \`logs/\` when it cleans up the rest of this home.

## Build output

Put build output under TMPDIR, for example xcodebuild -derivedDataPath "$TMPDIR/DerivedData". Never put it in this home's artifacts/ folder. Panels sends everything in artifacts/ to the home server. Put only files that a reviewer needs there.
`;
}

// Panels sets a home up once. It creates each file or folder only when it is missing
// and never changes it after that, so an agent's own edits last across restarts.
export async function prepare_agent_home(folder: string, role: AgentHomeRole): Promise<void> {
  await mkdir(join(folder, LOGS_DIRECTORY), { recursive: true });
  if (role === 'manager') await mkdir(join(folder, RULES_DIRECTORY), { recursive: true });
  const text = agent_home_text(role);
  for (const name of instruction_file_names())
    await writeFile(join(folder, name), text, { flag: 'wx' }).catch(
      (error: NodeJS.ErrnoException) => {
        if (error.code !== 'EEXIST') throw error;
      },
    );
}
