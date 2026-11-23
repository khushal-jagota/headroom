/**
 * Removed commands and options, each with its stated replacement. A removed name is not
 * declared in the tree. The parser prints these lines instead of guessing by spelling.
 */
const PROPOSE = 'Use: panels ticket propose < PROPOSAL_FILE';
const APPROVE = 'Use: panels ticket approve TICKET_ID --ceiling STAGE';
const SEND_BACK = 'Use: panels ticket send-back TICKET_ID < GUIDANCE_FILE';
const WORKER = ['Use: panels ticket show [PART_NAMES]', PROPOSE, 'Use: panels recap < RECAP_FILE'];
const HELP = ['Use: panels send-message --owner|--chief|--manager MANAGER_ID < MESSAGE_FILE'];

/** Command path -> the lines printed after the error. A function reads the remaining words. */
export const REMOVED_COMMANDS: Record<string, readonly string[] | ((rest: string[]) => string[])> =
  {
    worker: WORKER,
    'worker my-ticket': [WORKER[0]],
    'worker propose': [PROPOSE],
    'worker recap': [WORKER[2]],
    'worker request-help': HELP,
    'ticket request-help': HELP,
    'ticket proposal': (rest) =>
      rest.includes('accept')
        ? [APPROVE]
        : rest.includes('revise')
          ? [SEND_BACK]
          : rest.includes('submit')
            ? [PROPOSE]
            : [APPROVE, SEND_BACK, PROPOSE],
    'manager workspace': [
      'Use: panels manager show MANAGER_ID',
      'Use: panels ticket list --manager MANAGER_ID',
    ],
    'manager add-ticket': ['Use: panels ticket assign TICKET_ID --manager MANAGER_ID'],
    'manager remove-ticket': ['Use: panels ticket assign TICKET_ID --chief'],
    'manager conversation send': ['Use: panels send-message --manager MANAGER_ID'],
    'manager conversation reset': ['Reset a Manager conversation in the browser.'],
    'manager conversation': [
      'Use: panels send-message --manager MANAGER_ID',
      'Reset a Manager conversation in the browser.',
    ],
    'ticket employee-configuration': [
      'Use: panels ticket restart-worker TICKET_ID --backend BACKEND --model MODEL',
    ],
    'server-event-loop-tasks': ['No panels command replaces it.'],
  };

/** `command path` + ` ` + removed flag -> the line printed after the error. */
export const REMOVED_OPTIONS: Record<string, string> = {
  'ticket list --active': 'Live Tickets are the default.',
  'ticket list --include-terminal': 'Use --all or --done.',
  'ticket list --backlogged': 'Use --backlog.',
  'ticket list --not-backlogged': 'Live Tickets are the default.',
  'ticket list --ticket-status': 'Use --needs NEED.',
  'ticket list --exclude-ticket-status': 'Use --needs NEED.',
  'manager list --active': 'Active Managers are the default.',
};

/** The longest removed path that `words` starts with, and the lines it prints. */
export function removed_command(words: string[]): { path: string; lines: string[] } | null {
  for (let length = Math.min(words.length, 3); length > 0; length--) {
    const path = words.slice(0, length).join(' ');
    const entry = REMOVED_COMMANDS[path];
    if (entry === undefined) continue;
    return { path, lines: typeof entry === 'function' ? entry(words.slice(length)) : [...entry] };
  }
  return null;
}
