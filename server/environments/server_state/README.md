# Server state inventory

The server folder is the database's parent directory. Its portable state consists of the
checkpointed database, the credential encryption key, served files, skill files, and
`secrets/` for push and update credentials. Machine configuration is recreated separately.

`server_state_manifest(paths)` returns a JSON object whose keys are relative paths and
whose values are lowercase SHA256 digests. `server_state_valid(paths, recorded)` checks
the exact same set and bytes after a move. Both use the file digest, relative manifest,
and exact comparison in `checksums.ts`, which the existing backup-v2 verifier also uses.
The nightly backup retains its existing database, Worker settings, and skills scope and
its existing metadata format.

Pass the actual `server_root`, `database_path`, and `credential_key_path`. Also pass
`apns_key_path` and `update_token_path` when configured. An external credential key or
push/update secret causes refusal. Put those files within the server folder before a
move. Push and update secrets must reside beneath `secrets/`. The credential key can
use another path within the folder. Missing unconfigured roots and a key that does not
yet exist contribute no entries.

Stop the source and checkpoint SQLite before a full inventory. The API refuses a
nonempty WAL but does not acquire server ownership or stop a server. Keep the source
unchanged throughout inventory and transfer. This module reads only and transfers
nothing. The move owner must preserve private directory and secret-file permissions.

A running server passes `{ database_copy }` as the second argument instead. With a path
to a checked copy of the database, the database entry keeps the database's relative
name and takes the copy's digest, and the live WAL is allowed. With `null`, the list has
no database entry. The server move uses `null` for its first copy while the server runs,
and the copy it makes in its pause for the complete list.

Only the explicit state paths enter the full inventory. Logs, locks, sockets, process
records, update attempts, metrics, caches, backups, provider logins, and transfer staging
are excluded. The served tree omits Ticket worktrees and their ownership markers through
the canonical private-name rule. All skill files are included, including `SKILL.md`:
managed rows recreate that file, but unmanaged disk-only skills can also own it.
The skills publication lock, recognized skill atomic-write temporary files, and
internal conversation upload temporary files stay out. Files with arbitrary artifact
names remain state, even when a name resembles a temporary file. Review crash residue
in the served tree before a move.

A complete state inventory refuses included links and special files instead of silently
omitting them. Normalize such files before a move. Backup-v2 retains its historical
behavior of omitting links from its checksum maps.

For example, an operator module can write and verify the JSON artifact with these APIs:

```ts
import { server_state_manifest, server_state_valid } from './inventory.ts';

const paths = {
  server_root: '/srv/panels/data',
  database_path: '/srv/panels/data/planner.db',
  credential_key_path: '/srv/panels/data/credential-secrets/key',
  apns_key_path: '/srv/panels/data/secrets/push.p8',
  update_token_path: '/srv/panels/data/secrets/update-token',
};
const manifest = await server_state_manifest(paths);
const json = JSON.stringify(manifest, null, 2) + '\n';
// Persist json separately from the state roots and carry it with the move.
const valid = await server_state_valid(paths, JSON.parse(json));
```
