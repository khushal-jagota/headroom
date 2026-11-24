/** Only the peer's file byte routes are simulated; the home owns all other routes. */
import assert from 'node:assert/strict';
import { PROTOCOL_VERSION } from '../../../execution_configuration.ts';
import { createHash } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { decode_bounded } from '../../protocol.ts';

export const file_peer_bytes = Buffer.from('native transfer: λ and 雪\n'.repeat(5000));
export const file_peer_digest = createHash('sha256').update(file_peer_bytes).digest('hex');
export const file_peer_download = '/api/executor/commands/rehearsal-command/files/rehearsal-file';

export function native_file_peer(credential: string, incarnation: string) {
  let uploads = 0,
    downloads = 0;
  return {
    async handle(request: IncomingMessage, response: ServerResponse): Promise<boolean> {
      const path = request.url?.split('?')[0];
      if (path !== file_peer_download && path !== `/api/executor/ticket-files/${file_peer_digest}`)
        return false;
      assert.equal(request.headers['x-panels-executor-id'], 'test-executor');
      assert.equal(request.headers['x-panels-executor-credential'], credential);
      assert.equal(request.headers['x-panels-executor-incarnation'], incarnation);
      assert.equal(request.headers['x-panels-executor-protocol'], String(PROTOCOL_VERSION));
      assert.equal(request.headers['x-panels-executor-platform'], 'darwin');
      if (path === file_peer_download) {
        assert.equal(request.method, 'GET');
        response.writeHead(200, {
          'Content-Type': 'application/octet-stream',
          'Content-Length': String(file_peer_bytes.length),
          'X-Panels-File-SHA256': file_peer_digest,
        });
        response.write(file_peer_bytes.subarray(0, 32767));
        response.end(file_peer_bytes.subarray(32767));
        downloads++;
      } else {
        assert.equal(request.method, 'PUT');
        assert.equal(request.headers['content-length'], String(file_peer_bytes.length));
        assert.equal(request.headers['content-encoding'], undefined);
        const descriptor = request.headers['x-panels-file-upload'];
        assert.equal(typeof descriptor, 'string');
        const manifest = decode_bounded(
          Buffer.from(descriptor as string, 'base64url'),
          'FileManifest',
        );
        assert.deepEqual(manifest, {
          scope: 'ticket',
          owner_id: 'rehearsal-ticket',
          relative_path: 'artifacts/rehearsal.bin',
          sha256: file_peer_digest,
          byte_count: file_peer_bytes.length,
        });
        const chunks: Buffer[] = [];
        let count = 0;
        for await (const chunk of request) {
          const bytes = Buffer.from(chunk as Uint8Array);
          count += bytes.length;
          assert.ok(count <= file_peer_bytes.length);
          chunks.push(bytes);
        }
        const uploaded = Buffer.concat(chunks);
        assert.deepEqual(uploaded, file_peer_bytes);
        assert.equal(createHash('sha256').update(uploaded).digest('hex'), file_peer_digest);
        response.writeHead(204).end();
        uploads++;
      }
      return true;
    },
    result() {
      assert.equal(downloads, 1);
      assert.equal(uploads, 1);
      return {
        peer: 'file-route test double',
        uploads,
        downloads,
        sha256: file_peer_digest,
        byte_count: file_peer_bytes.length,
        real_linux_home_file_intake: 'not exercised; live check remains after the switch',
      };
    },
  };
}
