/** Offline source-author guidance for the JavaScript contract; starts no runtime. */
import { DEFAULT_LIMITS } from './configuration.ts';
import type { RuntimeLimits } from './contracts.ts';
import {
  CREATE as CREATE_REFERENCE,
  SCRIPT as SCRIPT_REFERENCE,
  RUN as RUN_REFERENCE,
  SCRIPT_NOTES,
  RUN_NOTES,
} from './request_guidance_data.ts';

export const SCALAR_EXAMPLE = `export default async function ({context, request, record}) {
  const response = await request('https://api.example.com/value', {
    credential_ref: 'example-service',
  });
  response.raise_for_status();
  const output = context.required_outputs[0];
  await record(output.measure_id, output.required_date, (await response.json()).value);
}
`;

/** Helper, context, example, and note fields for CLI guidance. */
export function runtime_guidance(limits: RuntimeLimits = DEFAULT_LIMITS) {
  return {
    helpers: {
      request: {
        signature:
          "await request(url, {credential_ref, method = 'GET', json_body, headers} = {}) -> response",
        notes:
          'Uses a named local credential when supplied. The parent resolves the credential through a private channel. ' +
          'The response supplies status, headers, raise_for_status(), await json(), and await text(). ' +
          'HTTP status does not raise automatically. Requests follow at most 20 redirects and add no automatic retries. ' +
          'With credential_ref or caller headers other than Accept and Content-Type, redirects must retain the scheme, host, and port. ' +
          'A different origin causes transport_failure before the next request. The error message names only the refused origin. ' +
          'Node fetch sets Host from the URL and ignores a caller-supplied Host, which Python HTTPX can retain. ' +
          'method, json_body, and headers support provider requests and pagination.',
      },
      record: {
        signature:
          'await record(measure_id, date, value, {unavailable_reason = null, query_start_at = null, query_end_at = null, limitation = null} = {}) -> void',
        notes:
          'Contributes a scalar candidate for an owned output and ISO date. ' +
          'A null value requires provider_has_no_history or provider_has_no_value. ' +
          'A finite numeric value requires no unavailable reason. ' +
          'Coverage bounds and limitation describe only that dated value.',
      },
      latest: {
        signature: 'await latest(measure_id) -> observation | null',
        notes:
          'Returns the latest retained numeric observation of an owned scalar output, captured at admission, or null. ' +
          'Fields: measure_id, date, value, run_id, observed_at, definition_revision, period_interval, ' +
          'manual_write_id, query_start_at, query_end_at, limitation, coverage_status, and coverage_reasons. ' +
          'period_interval is null or {start_date, end_date_exclusive}. Current candidates are not retained yet.',
      },
      record_dataset: {
        signature:
          'await record_dataset(dataset_id, date, rows, {query_end_at, query_start_at = null, labels = {}, unavailable_reason = null, limitation = null}) -> void',
        notes:
          'Contributes a date result for an owned declared dataset. Each row supplies ' +
          'dimensions {dimension: code} and values {field: number} that match the captured declaration. ' +
          'query_end_at is required Unix seconds. labels maps dimensions to {code: label}. ' +
          'Unavailable results have no rows. An empty successful result replaces that date with no rows. ' +
          'Omitted dates retain prior results or remain missing. Test receipts retain dataset candidates.',
      },
    },
    context: {
      run_id: 'Current source run ID.',
      frozen_at: 'Captured UTC Unix seconds. This value does not establish query coverage.',
      required_outputs:
        'Owned scalar declarations: measure_id, definition_revision, kind, timezone, period, required_date, ' +
        'and nullable period_interval. A present interval supplies start_date and end_date_exclusive.',
      dataset_outputs:
        'Owned dataset declarations: dataset_id, definition_revision, timezone, dimensions, fields, and required_date. ' +
        'Dimensions supply name/label. Fields supply name/label/unit/kind.',
      history:
        'null or {first_date, last_date}, the requested inclusive ISO date window. ' +
        'The runtime does not fetch provider history or fill missing dates automatically.',
    },
    examples: { scalar_request: SCALAR_EXAMPLE },
    notes: [
      'Write an ES module with exactly one export: export default async function ' +
        '({context, request, latest, record, record_dataset}). The context is deeply frozen. ' +
        'Await all helpers. The return value is unused. Printing JSON does not emit dated outputs.',
      "Use credential_ref: 'reusable-name' or {scheme: 'local', name: 'reusable-name'}. " +
        'Source Project and account are metadata, not credential scopes. ' +
        'Never embed secrets in scripts, output, or errors.',
      'The scalar_request example requires an existing owned taken measure and a matching named credential. ' +
        'Replace its provider URL and credential name. The example supplies no coverage facts or complete-period claim.',
      'required_date suggests a local collection date. It does not require every output or restrict valid ISO dates. ' +
        'Fetch and emit each desired historical date explicitly. Do not fabricate zero for unavailable values.',
      'Coverage bounds use finite Unix seconds for the interval that contributes to each dated result. ' +
        'Scalar start must precede end when both exist. A limitation is optional nonempty text. ' +
        'Missing coverage remains unknown. A zero or a historical date does not establish completeness.',
      'Candidates remain provisional until the parent validates both batches and completes the run atomically. ' +
        'Successful collection replaces supplied dates and retains other dates. ' +
        'Omitted coverage clears prior facts for each corrected date. A failed run discards all candidates. ' +
        'Test runs return candidates without changes to retained observations or collection health.',
      'Script submission uses source_revision as expected_revision. Run submission uses active_script.revision ' +
        'as expected_script_revision. Read the source before either operation. ' +
        'Inspect the returned run ID with panels measure-source run-show RUN_ID --json until completion.',
      `The default wall limit is ${limits.wall_seconds} seconds. The configured server wall limit remains authoritative. ` +
        `The run allows ${limits.output_bytes} combined control/stdout/stderr bytes and ` +
        `${limits.response_bytes} aggregate decoded HTTP response bytes. ` +
        `V8 old-space has a ${limits.heap_megabytes} MiB cap. This is not a total resident-memory guarantee.`,
      'The child has a private temporary directory and a filtered environment without provider homes or PLAN variables. Timezone, PATH, proxy, and CA variables use a fixed allowlist. ' +
        'The child runs as the server user and can read server-readable files. This is not a sandbox. ' +
        "Do not start processes, write outside the temporary directory, or read the server's files. " +
        'The built-in attention collector uses its explicit read-only database path.',
    ],
  };
}

/** Public offline guidance entry point. */
export const runtime_guide = runtime_guidance;

/** Offline request declarations share the JavaScript authoring contract. */
export const CREATE = CREATE_REFERENCE;
export const SCRIPT = {
  ...SCRIPT_REFERENCE,
  examples: {
    ...SCRIPT_REFERENCE.examples,
    scalar_script: {
      ...SCRIPT_REFERENCE.examples.scalar_script,
      text: SCALAR_EXAMPLE,
    },
  },
  notes: Object.values({
    ...SCRIPT_NOTES,
    submission:
      'Read the source first. Replace expected_revision with its current source_revision; ' +
      'send the object to panels measure-source submit-script SOURCE_ID on stdin. ' +
      'The text is a nonempty JavaScript ES module, saved immediately as a new active script revision.',
    runtime:
      'Before authoring or adapting the example, read panels measure-source runtime --json for ' +
      runtime_guidance().helpers.request.signature +
      ', context declarations, dated output helpers and coverage. Printing JSON is not output.',
  }),
};
export const RUN = {
  ...RUN_REFERENCE,
  notes: Object.values({
    ...RUN_NOTES,
    history:
      'history may be omitted or null; both default to null. A supplied object requires ' +
      'first_date and last_date as inclusive ISO calendar dates, with first_date not after last_date. ' +
      'The script receives context.history; the runtime does not fetch or fill history automatically. ' +
      'Replace example dates with the desired date window.',
  }),
};
