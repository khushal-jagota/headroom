The credential store retains Python's AES-256-GCM format and exact associated-data bytes. Public routes return metadata only. Credential requests and delivery remain with Unit 19.

`fixtures/python-cipher.json` is a small Python-produced oracle. Its key and values are public synthetic test data. They are never live credentials. The file pins bearer, header, and basic values, Unicode canonical JSON, and the legacy version-1 context. The server test checks exact canonical associated data and decryption through the TypeScript cipher. It needs no live Python implementation.

From the repository root, regenerate and inspect the fixture:

```sh
.venv/bin/python tests/compare/tools/generate_credential_fixture.py
git diff -- server/credentials/fixtures/python-cipher.json
```

The generator uses the current Python credential encoder and cipher, a temporary synthetic key, and deterministic synthetic nonces. It prints no values and reads no live state. Regeneration is deterministic. Keep this oracle for the TypeScript-only server suite after Python removal.

The separate live interoperability check stays in the comparison suite:

```sh
scripts/dev pytest tests/compare/direct/test_credential_interop.py
```

That single case uses a fresh test-local key and database. Python creates stored current and legacy credentials. `tests/compare/direct/credential_interop.ts` resolves them, checks associated data, replaces the legacy value, renames the current value, and creates a basic credential. Python resolves those TypeScript writes and replaces the basic value. TypeScript then resolves the Python replacement. Neither side prints a secret. The existing comparison workflow runs this case on Node 22 and 24 through its existing `pytest tests/compare/direct` step; no workflow change is required.

The server suite retains the fixed cipher oracle, wrong-key refusal, and private HTTP parser checks. Its existing shared database fixture uses Python's standard library to read literal schema declarations. The credential tests no longer spawn or import the Python credential implementation.

The generator and live-runtime driver stay outside `server/`, following the shared
comparison tooling layout. The server workflow receives no new Python setup.
