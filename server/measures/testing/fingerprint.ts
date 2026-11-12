/** Fixed-identity direct oracle companion. No database or server starts. */
import { readFileSync } from 'node:fs';
import { parse_numeric_json } from '../../core/http/numeric_json.ts';
import { fingerprint_bytes, fingerprint, parse_write } from '../logic/manual.ts';
const raw = JSON.parse(readFileSync(0, 'utf8')) as string[];
const result = raw.map((item) => {
  const body = parse_write(parse_numeric_json(item));
  const payload = Buffer.from(fingerprint_bytes('measure_fixed', body));
  return {
    bytes: payload.toString('hex'),
    text: payload.toString('utf8'),
    fingerprint: fingerprint('measure_fixed', body),
  };
});
process.stdout.write(JSON.stringify(result));
