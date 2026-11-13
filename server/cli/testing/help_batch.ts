/** Test-only help/error batch: production parser, no shell or environment protocol. */
import { run } from '../main.ts';

const chunks: Buffer[] = [];
for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
const cases: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'));
if (
  !Array.isArray(cases) ||
  !cases.every(
    (argv: unknown) => Array.isArray(argv) && argv.every((arg: unknown) => typeof arg === 'string'),
  )
)
  throw new Error('help batch requires argv string arrays');
const answers = [];
for (const argv of cases) {
  answers.push(await run(argv, process.env, async () => Buffer.alloc(0)));
}
process.stdout.write(JSON.stringify(answers));
