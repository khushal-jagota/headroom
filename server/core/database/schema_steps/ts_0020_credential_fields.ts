/**
 * A credential can hold named fields that a script reads and signs with, such as an App Store
 * Connect key ID, issuer ID and .p8 private key. The credentials table CHECK gains 'fields'.
 *
 * The table is rebuilt the standard SQLite way: create, copy, drop, rename, then recreate its
 * index and its three triggers with the seed's text. Every other constraint keeps its text.
 * No foreign key names this table, and no column is added.
 */
import type { SchemaStep } from './contracts.ts';

export const credential_fields_step: SchemaStep = {
  revision: 'ts_0020_credential_fields',
  previous_revision: 'ts_0019_live_activities',
  apply(tx) {
    tx.run(
      `CREATE TABLE credentials_new (
      id TEXT PRIMARY KEY, scheme TEXT NOT NULL CHECK(scheme='local'), name TEXT NOT NULL,
      auth_kind TEXT NOT NULL CHECK(auth_kind IN ('bearer','header','basic','fields')),
      encryption_version INTEGER NOT NULL CHECK(encryption_version IN (1,2)),
      encryption_context BLOB NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
      revision INTEGER NOT NULL CHECK(revision>0),
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)`,
    );
    tx.run(
      'INSERT INTO credentials_new (id,scheme,name,auth_kind,encryption_version,encryption_context,nonce,ciphertext,revision,created_at,updated_at) SELECT id,scheme,name,auth_kind,encryption_version,encryption_context,nonce,ciphertext,revision,created_at,updated_at FROM credentials',
    );
    tx.run('DROP TABLE credentials');
    tx.run('ALTER TABLE credentials_new RENAME TO credentials');
    tx.run('CREATE INDEX credentials_reference ON credentials(scheme,name)');
    tx.run(
      "CREATE TRIGGER \"resource_measures_credentials_delete\" AFTER DELETE ON credentials BEGIN INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES ('automatic-measures','all',1) ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END",
    );
    tx.run(
      "CREATE TRIGGER \"resource_measures_credentials_insert\" AFTER INSERT ON credentials BEGIN INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES ('automatic-measures','all',1) ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END",
    );
    tx.run(
      "CREATE TRIGGER \"resource_measures_credentials_update\" AFTER UPDATE ON credentials BEGIN INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES ('automatic-measures','all',1) ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END",
    );
  },
};
