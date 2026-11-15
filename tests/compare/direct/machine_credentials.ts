/** Private SQL comparison. Only nonsecret judgments leave this evaluator. */
import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { createDatabase } from "../../../server/core/database/index.ts";
import { ORIGINAL_MAC_MACHINE_ID } from "../../../server/execution_identity.ts";
import * as authority from "../../../server/machines/executor_credentials.ts";
import { PlannerError } from "../../../server/core/contracts.ts";

const database = createDatabase(process.argv[2]);
const incarnation = "11111111-1111-4111-8111-111111111111";
let closed = false;
const warnings: unknown[][] = [];
const original_warn = console.warn;
console.warn = (...args: unknown[]) => {
  warnings.push(args);
};
function code(work: () => unknown): string {
  try {
    work();
    return "accepted";
  } catch (error) {
    if (!(error instanceof PlannerError)) throw error;
    return error.code;
  }
}
const auth = (
  machine: string,
  credential: string | null,
  original: string | null = null,
) =>
  database.write((tx) =>
    authority.authenticate_executor(tx, machine, credential, original),
  );
try {
  const rollback = new Error("fixture rollback");
  assert.throws(
    () =>
      authority.issue_original_credential(
        {
          write: (work) =>
            database.write((tx) => {
              work(tx);
              throw rollback;
            }),
        },
        incarnation,
      ),
    (error) => error === rollback,
  );
  const first = database.write((tx) =>
    authority.register_machine(tx, "First", "linux"),
  );
  const second = database.write((tx) =>
    authority.register_machine(tx, "Second"),
  );
  const cross_machine = code(() => auth(second.machine_id, first.credential));
  const no_remote_legacy = code(() => auth(first.machine_id, null));
  const rotated = database.write((tx) =>
    authority.replace_credential(tx, first.machine_id),
  );
  const old_refused = code(() => auth(first.machine_id, first.credential));
  const rotation_valid =
    auth(first.machine_id, rotated.credential) === first.machine_id;
  const grant = authority.issue_original_credential(database, incarnation);
  const wrong_original = code(() => auth("original", "wrong", "original"));
  auth("original", grant.credential, "original");
  const original_row = () =>
    database.read((sql) =>
      sql.get<{
        pending_credential: string | null;
        legacy_closed_at_ms: number | null;
        credential_sha256: string;
      }>("SELECT * FROM machine_executor_credentials WHERE machine_id=?", [
        ORIGINAL_MAC_MACHINE_ID,
      ]),
    )!;
  const before_close = original_row();
  const auth_keeps_pending =
    before_close.pending_credential === grant.credential &&
    before_close.legacy_closed_at_ms === null;
  const legacy_after_auth =
    auth("original", null, "original") === ORIGINAL_MAC_MACHINE_ID;
  const retry_after_auth = authority.issue_original_credential(
    database,
    "22222222-2222-4222-8222-222222222222",
  );
  const retry_after_auth_same =
    JSON.stringify(retry_after_auth) === JSON.stringify(grant);
  const wrong_close = code(() =>
    authority.close_original_handover(
      database,
      ORIGINAL_MAC_MACHINE_ID,
      "wrong",
      incarnation,
    ),
  );
  for (let attempt = 0; attempt < 2; attempt++) {
    authority.close_original_handover(
      database,
      ORIGINAL_MAC_MACHINE_ID,
      grant.credential,
      "not-a-uuid",
    );
  }
  const row = original_row();
  const retired_pending =
    row.pending_credential === null && row.legacy_closed_at_ms !== null;
  const verifier_matches =
    row.credential_sha256 ===
    createHash("sha256").update(grant.credential).digest("hex");
  const closed_reissue = code(() =>
    authority.issue_original_credential(database, incarnation),
  );
  assert.deepEqual(warnings, [
    [
      "Original executor credential issued; awaiting first credential authentication",
      {
        issued_at_ms: grant.issued_at_ms,
        issued_incarnation: incarnation,
        machine_id: ORIGINAL_MAC_MACHINE_ID,
      },
    ],
    [
      "Original executor credential confirmed; legacy authority permanently closed",
      {
        closed_at_ms: row.legacy_closed_at_ms,
        issued_at_ms: grant.issued_at_ms,
        issued_incarnation: incarnation,
        authenticated_incarnation: null,
        machine_id: ORIGINAL_MAC_MACHINE_ID,
      },
    ],
  ]);
  const audit_warnings = true;
  const no_audit_secrets =
    !JSON.stringify(warnings).includes(grant.credential) &&
    !JSON.stringify(warnings).includes(row.credential_sha256);
  database.close();
  closed = true;
  const reopened = createDatabase(process.argv[2]);
  let durable_closure: string;
  try {
    durable_closure = code(() =>
      reopened.write((tx) =>
        authority.authenticate_executor(tx, "original", null, "original"),
      ),
    );
  } finally {
    reopened.close();
  }
  process.stdout.write(
    JSON.stringify({
      cross_machine,
      no_remote_legacy,
      old_refused,
      rotation_valid,
      wrong_original,
      auth_keeps_pending,
      legacy_after_auth,
      retry_after_auth_same,
      wrong_close,
      retired_pending,
      verifier_matches,
      closed_reissue,
      audit_warnings,
      no_audit_secrets,
      durable_closure,
    }),
  );
} finally {
  console.warn = original_warn;
  if (!closed) database.close();
}
