The credential store keeps its AES-256-GCM format and exact associated-data bytes, so stored credentials stay readable. Public routes return metadata only. Credential requests and delivery live in `requests/`.

`fixtures/python-cipher.json` is a small fixed oracle that the earlier server produced. Its key and values are public synthetic test data. They are never live credentials. The file pins bearer, header, and basic values, Unicode canonical JSON, and the legacy version-1 context. The server test checks exact canonical associated data and decryption through the TypeScript cipher. Nothing regenerates it.

The server suite retains the fixed cipher oracle, wrong-key refusal, and private HTTP parser checks.
