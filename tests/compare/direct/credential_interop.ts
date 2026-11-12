/** Live-store boundary used only by the Python comparison suite. Never emits credentials. */
import { createDatabase } from "../../../server/core/database/index.ts";
import { OWNER } from "../../../server/core/contracts.ts";
import {
  create_credential_in_transaction,
  rename_credential,
  replace_credential_in_transaction,
} from "../../../server/credentials/actions.ts";
import {
  DatabaseCredentialResolver,
  FileCredentialCipher,
} from "../../../server/credentials/store.ts";
import { read_encryption_context } from "../../../server/credentials/data.ts";
import { associated_data } from "../../../server/credentials/logic/validation.ts";

const [path, key, mode] = process.argv.slice(2);
if (!path || !key || !["mutate", "verify"].includes(mode ?? "")) {
  throw new Error("credential interoperability arguments are invalid");
}
const database = createDatabase(path);
function check(value: boolean): void {
  if (!value) throw new Error("credential interoperability mismatch");
}
function resolve(name: string) {
  return database.read((sql) =>
    new DatabaseCredentialResolver(
      sql,
      new FileCredentialCipher(key!, true),
    ).resolve({
      scheme: "local",
      name,
    }),
  );
}
try {
  if (mode === "mutate") {
    const bearer = resolve("café 😀");
    const header = resolve("legacy");
    check(
      bearer.secret.kind === "bearer" &&
        bearer.secret.token === "synthetic-token",
    );
    check(
      header.secret.kind === "header" &&
        header.secret.header_name === "X-Demo" &&
        header.secret.value === "synthetic-header",
    );
    database.read((sql) => {
      check(
        read_encryption_context(sql, bearer.metadata.id).associated_data.equals(
          associated_data(bearer.metadata),
        ),
      );
    });
    const legacyContext = database.read(
      (sql) => read_encryption_context(sql, header.metadata.id).associated_data,
    );
    const options = { principal: OWNER, now: 101, key_path: key };
    database.write((tx) =>
      replace_credential_in_transaction(
        tx,
        header.metadata.id,
        { kind: "header", header_name: "X-Demo", value: "synthetic-replaced" },
        1,
        options,
      ),
    );
    database.read((sql) => {
      const context = read_encryption_context(sql, header.metadata.id);
      check(
        context.version === 1 && context.associated_data.equals(legacyContext),
      );
    });
    const renamed = database.write((tx) =>
      rename_credential(tx, bearer.metadata.id, "renamed 😀", 1, options),
    );
    database.read((sql) =>
      check(
        read_encryption_context(sql, renamed.id).associated_data.equals(
          associated_data(renamed),
        ),
      ),
    );
    database.write((tx) =>
      create_credential_in_transaction(
        tx,
        {
          reference: { scheme: "local", name: "ts-basic" },
          auth_kind: "basic",
          secret: { kind: "basic", username: "", password: "synthetic-basic" },
        },
        options,
      ),
    );
  } else {
    const basic = resolve("ts-basic");
    check(basic.metadata.revision === 2);
    check(
      basic.secret.kind === "basic" &&
        basic.secret.username === "demo" &&
        basic.secret.password === "synthetic-python-replaced",
    );
  }
} finally {
  database.close();
}
