/** Streaming HTTP never reopens a transfer path after its worker retains the inode. */
import type { FileUploadRequest } from '../../wire.generated.ts';
import { encode_bounded, integer_value, ValueError } from '../../protocol.ts';
import type { FilesService } from '../files.ts';
import type { TransferHandle } from '../file_worker_contracts.ts';
import {
  FILE_TRANSFER_STALL_SECONDS,
  MAX_ENVELOPE_BYTES,
  TRANSFER_CHUNK_BYTES,
} from '../configuration.ts';
import { http_error } from './errors.ts';
import { read_bounded } from './sessions.ts';
import type { MacHttpClient } from './http.ts';
const sha256 = /^[0-9a-f]{64}$/;
export class MacFileTransport {
  private readonly client: MacHttpClient;
  private readonly files: FilesService;
  constructor(client: MacHttpClient, files: FilesService) {
    this.client = client;
    this.files = files;
  }
  private async own_transfer<T>(
    file: TransferHandle,
    operation: () => Promise<T>,
    discard_failure = false,
  ): Promise<T> {
    let primary: unknown;
    try {
      return await operation();
    } catch (error) {
      primary = error;
      throw error;
    } finally {
      try {
        await this.files.close_transfer(file, discard_failure && primary !== undefined);
      } catch (cleanup) {
        if (primary !== undefined)
          throw new AggregateError([primary, cleanup], 'Transfer and native cleanup failed.');
        throw cleanup;
      }
    }
  }
  async upload_file(
    upload: FileUploadRequest,
    source: string,
    signal?: AbortSignal,
  ): Promise<void> {
    const manifest = upload.manifest,
      count = integer_value(manifest.byte_count);
    if (!sha256.test(manifest.sha256) || count < 0n)
      throw new ValueError('invalid upload manifest');
    const file = await this.files.open_transfer(source, 'read');
    const files = this.files;
    async function* chunks(): AsyncIterable<Uint8Array> {
      let sent = 0n;
      for (;;) {
        signal?.throwIfAborted();
        const remaining = count - sent;
        const bytes = await files.read_transfer(
          file,
          remaining === 0n
            ? 1
            : Number(
                remaining < BigInt(TRANSFER_CHUNK_BYTES) ? remaining : BigInt(TRANSFER_CHUNK_BYTES),
              ),
        );
        if (!bytes.length) {
          if (remaining !== 0n) throw new ValueError('snapshot shorter than manifest');
          break;
        }
        sent += BigInt(bytes.length);
        if (sent > count) throw new ValueError('snapshot exceeds manifest');
        yield bytes;
      }
      try {
        await files.finish_transfer(file, manifest.sha256, count);
      } catch (error) {
        if (error instanceof Error && error.name === 'ValueError')
          throw new ValueError('snapshot digest differs from manifest');
        throw error;
      }
    }
    await this.own_transfer(file, async () => {
      await this.client.sessions.request(
        {
          loop: 'items',
          method: 'PUT',
          path: `/api/executor/uploads/${manifest.sha256}`,
          body: chunks(),
          // The header retains the exact integer. The producer checks its own exact count below.
          headers: {
            'Content-Type': 'application/octet-stream',
            'Content-Length': String(count),
            'X-Panels-File-Upload': Buffer.from(
              encode_bounded(upload, 'FileUploadRequest'),
            ).toString('base64url'),
          },
          stall_timeout: FILE_TRANSFER_STALL_SECONDS,
          signal,
        },
        this.client.headers,
        async (response) => {
          if (response.statusCode !== 204)
            throw http_error(
              response.statusCode!,
              await read_bounded(response, MAX_ENVELOPE_BYTES),
            );
          await read_bounded(response, MAX_ENVELOPE_BYTES);
        },
      );
    });
  }
  async download_command_file(
    command_id: string,
    stored_file_id: string,
    target: string,
    signal?: AbortSignal,
  ): Promise<string> {
    const file = await this.files.open_transfer(target, 'write');
    return this.own_transfer(
      file,
      async () => {
        return await this.client.sessions.request(
          {
            loop: 'downloads',
            method: 'GET',
            path: `/api/executor/commands/${encodeURIComponent(command_id)}/files/${encodeURIComponent(stored_file_id)}`,
            read_timeout: FILE_TRANSFER_STALL_SECONDS,
            signal,
          },
          this.client.headers,
          async (response) => {
            if (response.statusCode !== 200)
              throw http_error(
                response.statusCode!,
                await read_bounded(response, MAX_ENVELOPE_BYTES),
              );
            const digest = response.headers['x-panels-file-sha256'],
              length = response.headers['content-length'];
            if (
              response.headers['content-encoding'] ||
              typeof digest !== 'string' ||
              !sha256.test(digest) ||
              typeof length !== 'string' ||
              !/^\d+$/.test(length)
            )
              throw new ValueError('executor download lacks its length or digest');
            const expected = BigInt(length);
            let count = 0n;
            for await (const raw of response) {
              signal?.throwIfAborted();
              const bytes = Buffer.from(raw as Uint8Array);
              count += BigInt(bytes.length);
              if (count > expected) throw new ValueError('download exceeds its length');
              for (let offset = 0; offset < bytes.length; offset += TRANSFER_CHUNK_BYTES)
                await this.files.write_transfer(
                  file,
                  bytes.subarray(offset, offset + TRANSFER_CHUNK_BYTES),
                );
            }
            if (count !== expected) throw new ValueError('download is short or corrupt');
            try {
              await this.files.finish_transfer(file, digest, expected);
            } catch (error) {
              if (error instanceof Error && error.name === 'ValueError')
                throw new ValueError('download is short or corrupt');
              throw error;
            }
            return digest;
          },
        );
      },
      true,
    );
  }
}
