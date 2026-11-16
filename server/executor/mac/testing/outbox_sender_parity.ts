import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { OutboxStore } from '../storage/outbox.ts';
import { MacOutboxSender } from '../transport/outbox.ts';
import { decode_json, decode_value, encode_bounded } from '../../protocol.ts';

export async function deliver(input: { root: string; replies: Record<string, (string | null)[]> }) {
  const options = { deadline: performance.now() / 1000 + 10 };
  const clock = { monotonic: () => 0, now_ns: () => 0n };
  const store = await OutboxStore.open(join(input.root, 'spool'), clock, options, () => {});
  const calls: Record<string, string[]> = {},
    uploaded: string[][] = [],
    confirmed: (string | boolean)[][] = [];
  let finish!: () => void;
  const drained = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const sender = new MacOutboxSender({
    store,
    client: {
      async post_items(id, batch) {
        (calls[id] ??= []).push(
          Buffer.from(encode_bounded(batch, 'ItemBatch', { limit: 4 * 1024 * 1024 })).toString(),
        );
        const reply = input.replies[id]!.shift();
        if (reply == null) throw new Error('offline');
        const response = decode_json(reply, 'ItemBatchReply');
        // A server can emit the same manifest with a different property order.
        if (response.stop?.missing_files)
          response.stop.missing_files = response.stop.missing_files.map((manifest) =>
            decode_value(Object.fromEntries(Object.entries(manifest).reverse()), 'FileManifest'),
          );
        return response;
      },
    },
    files: {
      async upload_file(request, source) {
        uploaded.push([
          Buffer.from(encode_bounded(request, 'FileUploadRequest')).toString(),
          createHash('sha256').update(readFileSync(source)).digest('hex'),
        ]);
      },
    },
    notify_confirmed(receipt) {
      const row = store.lookup(receipt.submission_id);
      confirmed.push([receipt.submission_id, row === null || row.receipt_json !== null]);
      if (store.pending_count() === 0) finish();
    },
    warn() {},
    on_failure() {},
  });
  try {
    sender.start();
    await drained;
  } finally {
    await sender.stop(options);
    await store.stop(options);
  }
  return { calls, uploaded, confirmed: confirmed.sort() };
}
