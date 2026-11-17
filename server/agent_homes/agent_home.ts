import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

// The one setup that every Worker and Manager home shares. Ticket workspace
// preparation adds the checkout and artifacts/ after this.
export const LOGS_DIRECTORY = 'logs';
export type AgentHomeRole = 'worker' | 'manager';

const ROLE_LINES: Record<AgentHomeRole, string> = {
  worker:
    'You are the Worker for one Ticket. `worktree/` holds the code. `artifacts/` holds the files that a reviewer needs.',
  manager: 'You are one Manager.',
};

export function agent_home_text(role: AgentHomeRole): string {
  return `# This folder

This folder is your home in Panels. ${ROLE_LINES[role]}

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

// Panels owns CLAUDE.md and AGENTS.md and writes them each time, so a change
// to the text reaches every home at its next start.
export async function prepare_agent_home(folder: string, role: AgentHomeRole): Promise<void> {
  await mkdir(join(folder, LOGS_DIRECTORY), { recursive: true });
  const text = agent_home_text(role);
  await writeFile(join(folder, 'CLAUDE.md'), text);
  await writeFile(join(folder, 'AGENTS.md'), text);
}
