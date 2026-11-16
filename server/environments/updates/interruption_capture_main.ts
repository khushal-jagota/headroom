/** Fixed stopped-app helper. It does not import the application or public CLI. */
import { capture_stopped_database } from './interruption_capture.ts';
const args = process.argv.slice(2);
if (args.length !== 3) {
  process.stderr.write('expected database, deployment ID, capture ID\n');
  process.exitCode = 1;
} else {
  const result = capture_stopped_database(args[0]!, args[1]!, args[2]!);
  process.stdout.write(
    JSON.stringify({
      state: result.state,
      detail: Array.from(result.detail ?? '')
        .slice(0, 1000)
        .join(''),
    }) + '\n',
  );
}
