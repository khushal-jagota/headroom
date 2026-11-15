/** Private SQL comparison. Only nonsecret judgments leave this evaluator. */
import { createHash } from "node:crypto";
import { createDatabase } from "../../../server/core/database/index.ts";
import {
  HOME_MACHINE_ID,
  ORIGINAL_MAC_MACHINE_ID,
} from "../../../server/execution_identity.ts";
import * as authority from "../../../server/machines/executor_credentials.ts";
import { PlannerError } from "../../../server/core/contracts.ts";

const database = createDatabase(process.argv[2]);
const incarnation = "11111111-1111-4111-8111-111111111111";
let closed = false;
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
  const first = database.write((tx) =>
    authority.register_machine(tx, "First", "linux"),
  );
  const second = database.write((tx) =>
    authority.register_machine(tx, "Second"),
  );
  const cross_machine = code(() => auth(second.machine_id, first.credential));
  const no_remote_legacy = code(() => auth(first.machine_id, null));
  const platform = database.read((sql) =>
    authority.executor_platform(sql, first.machine_id),
  );
  const rotated = database.write((tx) =>
    authority.replace_credential(tx, first.machine_id),
  );
  const old_refused = code(() => auth(first.machine_id, first.credential));
  const rotation_valid =
    auth(first.machine_id, rotated.credential) === first.machine_id;
  const legacy_before =
    auth("original", null, "original") === ORIGINAL_MAC_MACHINE_ID;
  const grant = database.write((tx) =>
    authority.issue_original_credential(tx, incarnation),
  );
  const retry = database.write((tx) =>
    authority.issue_original_credential(
      tx,
      "22222222-2222-4222-8222-222222222222",
    ),
  );
  const retry_same = JSON.stringify(grant) === JSON.stringify(retry);
  const wrong_original = code(() => auth("original", "wrong", "original"));
  const legacy_after_wrong =
    auth("original", null, "original") === ORIGINAL_MAC_MACHINE_ID;
  const credential_accepted =
    auth("original", grant.credential, "original") === ORIGINAL_MAC_MACHINE_ID;
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
  const retry_after_auth = database.write((tx) =>
    authority.issue_original_credential(tx, incarnation),
  );
  const retry_after_auth_same =
    JSON.stringify(retry_after_auth) === JSON.stringify(grant);
  const wrong_close = code(() =>
    database.write((tx) =>
      authority.close_original_handover(
        tx,
        ORIGINAL_MAC_MACHINE_ID,
        "wrong",
        incarnation,
      ),
    ),
  );
  const legacy_after_wrong_close =
    auth("original", null, "original") === ORIGINAL_MAC_MACHINE_ID;
  database.write((tx) =>
    authority.close_original_handover(
      tx,
      ORIGINAL_MAC_MACHINE_ID,
      grant.credential,
      incarnation,
    ),
  );
  const row = original_row();
  const original_now = Date.now;
  Date.now = () => row.legacy_closed_at_ms! + 1000;
  try {
    database.write((tx) =>
      authority.close_original_handover(
        tx,
        ORIGINAL_MAC_MACHINE_ID,
        grant.credential,
        incarnation,
      ),
    );
  } finally {
    Date.now = original_now;
  }
  const closure_idempotent =
    JSON.stringify(row) === JSON.stringify(original_row());
  const retired_pending =
    row.pending_credential === null && row.legacy_closed_at_ms !== null;
  const verifier_matches =
    row.credential_sha256 ===
    createHash("sha256").update(grant.credential).digest("hex");
  const no_stored_secret = !JSON.stringify(row).includes(grant.credential);
  const legacy_after = code(() => auth("original", null, "original"));
  const closed_reissue = code(() =>
    database.write((tx) =>
      authority.issue_original_credential(tx, incarnation),
    ),
  );
  const home_refused = code(() => auth(HOME_MACHINE_ID, grant.credential));
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
      platform,
      old_refused,
      rotation_valid,
      legacy_before,
      retry_same,
      wrong_original,
      legacy_after_wrong,
      credential_accepted,
      auth_keeps_pending,
      legacy_after_auth,
      retry_after_auth_same,
      wrong_close,
      legacy_after_wrong_close,
      closure_idempotent,
      retired_pending,
      verifier_matches,
      no_stored_secret,
      legacy_after,
      closed_reissue,
      home_refused,
      durable_closure,
    }),
  );
} finally {
  if (!closed) database.close();
}
