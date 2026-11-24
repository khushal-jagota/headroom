import { expect, test } from 'vitest';
import { create_commands } from './commands.ts';
import { AUDIENCES, CliCommand, find_command } from './declarations.ts';
import { CliFailure } from './errors.ts';
import { parse } from './parser.ts';
import { REMOVED_COMMANDS, REMOVED_OPTIONS } from './removed.ts';

function failure(argv: string[], environment: NodeJS.ProcessEnv = {}) {
  try {
    parse(argv, create_commands, environment);
  } catch (error) {
    expect(error).toBeInstanceOf(CliFailure);
    return (error as CliFailure).result;
  }
  throw new Error(`expected a usage failure: ${argv.join(' ')}`);
}
const leaves = (command: CliCommand): CliCommand[] =>
  command.commands.length
    ? command.commands.flatMap((child) => leaves(child as CliCommand))
    : [command];

// The HTTP comparison can return the same empty page after integer rounding.
// Check actual declarations here to preserve exact large values and numeric validation.
test('integer options retain exact decimal values and reject invalid input', () => {
  const parsed = parse(
    ['project', 'list', '--offset', '9007199254740993', '--limit', '12'],
    create_commands,
  );
  expect(parsed.values).toMatchObject({ offset: 9007199254740993n, limit: 12 });
  expect(() => parse(['project', 'list', '--limit', 'not-a-number'], create_commands)).toThrow();
});

// Commander accepts bare negative operands, which would reach an HTTP handler
// instead of returning usage. The HTTP comparisons do not pin this distinction.
test('dash-prefixed operands require a terminator without changing option values', () => {
  const usage_failure = expect.objectContaining({
    result: expect.objectContaining({ exit_code: 2 }),
  });
  expect(() => parse(['ticket', 'show', '-1'], create_commands, {})).toThrow(usage_failure);
  expect(parse(['ticket', 'show', '--', '-1'], create_commands, {}).values.ticket_id).toBe('-1');
  expect(
    parse(['project', 'show', '--json', '--', '--json'], create_commands, {}).values.project_id,
  ).toBe('--json');
  expect(parse(['attention', 'summary', '--limit', '-1'], create_commands, {}).values.limit).toBe(
    -1,
  );
  expect(() =>
    parse(['project', 'set', '--value', '--', '-1', 'summary'], create_commands, {}),
  ).toThrow(usage_failure);
});

// Risk: a removed name answers with a spelling guess (ticket request-help -> request-recap)
// instead of its stated replacement. One loop over the stated table covers every entry.
test('every removed name prints its stated replacement and never a spelling guess', () => {
  const root = create_commands({});
  for (const [path, entry] of Object.entries(REMOVED_COMMANDS)) {
    const words = path.split(' ');
    expect(find_command(root, words), path).toBeUndefined();
    const variants: [string[], readonly string[]][] =
      typeof entry === 'function'
        ? ['accept', 'revise', 'submit', 'other'].map((word) => [
            [...words, 't_1', word],
            entry(['t_1', word]),
          ])
        : [[[...words, 'extra'], entry]];
    for (const [argv, stated] of variants) {
      const result = failure(argv);
      expect(result.exit_code).toBe(2);
      expect(result.stderr.split('\n').slice(1, -1), argv.join(' ')).toEqual(stated);
    }
  }
  expect(failure(['ticket', 'request-help']).stderr).not.toContain('request-recap');
  const guessed = failure(['ticket', 'shw']).stderr;
  expect(guessed).toContain('Commands under panels ticket: approve, assign,');
  expect(guessed).toContain('Did you mean: show?');
  expect(guessed).not.toContain('Use:');
  for (const [key, line] of Object.entries(REMOVED_OPTIONS)) {
    const words = key.split(' ');
    const result = failure([...words.slice(0, -1), words.at(-1)!]);
    expect(result.stderr.split('\n')[1], key).toBe(line);
  }
});

test('usage failures keep recovery, bare groups print help, and help omits false defaults', () => {
  expect(failure(['ticket', 'list', '--bogus']).stderr).toBe(
    'error: "--bogus" is not an option for panels ticket list.\nUse: panels ticket list [OPTIONS]\n',
  );
  expect(failure(['manager', 'page']).stderr).toContain('Commands under panels manager:');
  const bare = parse(['ticket'], create_commands, {});
  expect(bare.help).toContain('Usage: panels ticket');
  expect(bare.help).toContain('send-back');
  expect(parse(['ticket', 'list', '--help'], create_commands, {}).help).not.toContain(
    '(default: false)',
  );
  expect(parse(['ticket', 'list'], create_commands, {}).values.as_json).toBe(false);
});

// Risk: a command added without an audience or with a tutorial description. One loop
// over the whole tree, cheaper than any help comparison.
test('every command has a one-line description and every leaf has an audience', () => {
  const root = create_commands({});
  const all = (command: CliCommand): CliCommand[] => [
    command,
    ...command.commands.flatMap((child) => all(child as CliCommand)),
  ];
  for (const command of all(root)) {
    const description = command.description();
    expect(description.trim(), command.path.join(' ')).not.toBe('');
    expect(description, command.path.join(' ')).not.toContain('\n');
  }
  for (const leaf of leaves(root)) expect(AUDIENCES, leaf.path.join(' ')).toContain(leaf.audience);
});

// Risk: an agent's root help shows host commands or omits its own. The card names paths,
// so a renamed command would also throw here.
test('root help is the role card for an agent and the full tree without one', () => {
  const worker = parse(['--help'], create_commands, { PLAN_ACTOR: 'worker' }).help;
  for (const path of ['ticket show', 'ticket propose', 'recap', 'send-message'])
    expect(worker).toContain(`panels ${path} `);
  expect(worker).toContain('Your Ticket:');
  expect(worker).not.toMatch(/\b(environment|executor|stalls|serve|restart)\b/);
  const chief = parse([], create_commands, { PLAN_ACTOR: 'chief' }).help;
  for (const job of ['What needs whom:', 'Decide:', 'Create and steer:', 'Your own record:'])
    expect(chief).toContain(job);
  expect(chief).toContain('panels ticket approve ');
  const operator = parse(['--help'], create_commands, {}).help;
  for (const group of ['environment', 'executor', 'serve', 'stalls', 'ticket'])
    expect(operator).toContain(`  ${group}`);
  expect(operator).not.toContain('  api');
});
