/** `panels --help` for an agent: the commands of its role, grouped by job. */
import { find_command, type CliCommand } from './declarations.ts';

type Card = readonly (readonly [job: string, paths: readonly string[]])[];
const WORKER_CARD: Card = [
  [
    'Your Ticket',
    [
      'ticket show',
      'ticket propose',
      'recap',
      'preview prepare',
      'preview register',
      'preview show',
      'preview stop',
    ],
  ],
  ['Talk', ['send-message']],
  ['Look around', ['ticket list', 'ticket history', 'manager list', 'manager show']],
];
const READS: Card[number] = [
  'What needs whom',
  ['review', 'ticket list', 'ticket show', 'ticket history', 'manager list', 'manager show'],
];
const DECIDE: Card[number] = ['Decide', ['ticket approve', 'ticket send-back', 'ticket complete']];
const STEER = [
  'ticket schema',
  'ticket create',
  'ticket edit',
  'ticket block',
  'ticket unblock',
  'ticket backlog',
  'ticket resume',
  'ticket delete',
  'ticket restart-worker',
  'ticket request-recap',
];
/** A Manager acts on its own record and on the Tickets assigned to it. */
const MANAGER_CARD: Card = [
  READS,
  DECIDE,
  ['Create and steer', STEER],
  ['Your own record', ['recap', 'manager set', 'page show', 'page edit', 'manager artifact write']],
  ['Talk', ['send-message']],
];
/** The Chief also moves Tickets and creates, asks, and deletes Managers. */
const CHIEF_CARD: Card = [
  READS,
  DECIDE,
  [
    'Create and steer',
    [
      ...STEER,
      'ticket assign',
      'manager create',
      'manager set',
      'manager request-recap',
      'manager delete',
    ],
  ],
  ['Your own record', ['recap', 'page show', 'page edit']],
  ['Talk', ['send-message']],
];
export const ROLE_CARDS: Record<'worker' | 'manager' | 'chief', Card> = {
  worker: WORKER_CARD,
  manager: MANAGER_CARD,
  chief: CHIEF_CARD,
};
export function role_of(environment: NodeJS.ProcessEnv): keyof typeof ROLE_CARDS | null {
  const actor = environment.PLAN_ACTOR?.trim();
  return actor === 'worker' || actor === 'manager' || actor === 'chief' ? actor : null;
}
/** The other groups an agent may use: not hidden, not only host commands, not all on the card. */
function other_groups(root: CliCommand, card: Card): string[] {
  const on_card = new Set(card.flatMap(([, paths]) => paths));
  const leaves = (command: CliCommand): CliCommand[] =>
    command.commands.length
      ? command.commands.flatMap((child) => leaves(child as CliCommand))
      : [command];
  return root.commands
    .filter((child) => !(child as CliCommand & { _hidden?: boolean })._hidden)
    .filter((child) =>
      leaves(child as CliCommand).some(
        (leaf) => leaf.audience !== 'host' && !on_card.has(leaf.path.join(' ')),
      ),
    )
    .filter((child) => child.commands.length)
    .map((child) => child.name());
}
export function role_card(root: CliCommand, role: keyof typeof ROLE_CARDS): string {
  const card = ROLE_CARDS[role];
  const rows = card.flatMap(([, paths]) => paths).map((path) => `panels ${path}`);
  const width = Math.max(...rows.map((row) => row.length)) + 2;
  const lines = [
    `Panels commands for a ${role === 'worker' ? 'Worker' : role === 'chief' ? 'Chief' : 'Manager'}.`,
  ];
  for (const [job, paths] of card) {
    lines.push('', `${job}:`);
    for (const path of paths) {
      const command = find_command(root, path.split(' '));
      if (!command) throw new Error(`role help names a missing command: ${path}`);
      lines.push(`  ${`panels ${path}`.padEnd(width)}${command.description()}`);
    }
  }
  lines.push(
    '',
    `Other groups: ${other_groups(root, card).join(', ')}. Run panels <group> --help for their commands.`,
  );
  return lines.join('\n') + '\n';
}
