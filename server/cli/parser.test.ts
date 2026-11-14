import { expect, test } from 'vitest';
import { create_commands } from './commands.ts';
import { CliFailure } from './errors.ts';
import { parse } from './parser.ts';

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

test('usage failures retain recovery and help omits false flag defaults', () => {
  const cases: [string[], string][] = [
    [['ticket', 'proposals'], 'Use: panels ticket proposal TICKET_ID ACTION'],
    [['ticket', 'shw'], 'Use: panels ticket show'],
    [['ticket', 'list', '--bogus'], 'Use: panels ticket list'],
    [['sprint'], 'No replacement panels command can be inferred for "sprint".'],
    [['day'], 'No replacement panels command can be inferred for "day".'],
    [['manager', 'page'], 'Use: panels page --help'],
    [['ticket', '--bogus'], 'No panels call can be named until an action is selected.'],
    [
      ['attention', 'setup-collection', '--bogus'],
      'Use: panels attention setup-collection --project TEXT --database-path PATH [OPTIONS]',
    ],
  ];
  for (const [argv, recovery] of cases) {
    let failure: unknown;
    try {
      parse(argv, create_commands, {});
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(CliFailure);
    const result = (failure as CliFailure).result;
    expect(result).toMatchObject({ exit_code: 2, stdout: '' });
    if (argv[1] === 'proposals')
      expect(result.stderr.split('\n')[0]).toBe(
        'error: "proposals" is not a command under panels ticket.',
      );
    if (argv[1] === 'list')
      expect(result.stderr.split('\n')[0]).toBe(
        'error: "--bogus" is not an option for panels ticket list.',
      );
    expect(result.stderr).toContain(recovery);
  }
  expect(parse(['ticket', 'list', '--help'], create_commands, {}).help).not.toContain(
    '(default: false)',
  );
  expect(parse(['ticket', 'list'], create_commands, {}).values.as_json).toBe(false);
});
