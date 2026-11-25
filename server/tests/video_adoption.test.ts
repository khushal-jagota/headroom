import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { video_send_admission } from '../conversation/video_send_admission.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import {
  MessageFile,
  message_content_from_stored,
  message_content_json_entries,
} from '../conversation/message_content.ts';
import { PromptDeliveryStarted } from '../conversation/contracts.ts';
import { OWNER } from '../core/contracts.ts';
import { fixture, fixture_path } from './ticket_fixture.ts';

// Distinct risk: a successful video send can point at missing bytes, or a retry
// can move an accepted file back out of the conversation. The 22 Ticket rule tests
// prove SQLite transitions but never cross the database/filesystem boundary.
// Reuse the existing schema fixture, with no browser, server, probe, or network.
test('video adoption publishes bytes and accepted retries preserve their conversation', async () => {
  const database = fixture();
  const service = new VideoUploadService(database, fixture_path(database));
  const destination = { kind: 'ticket' as const, id: 't_video', expected_conversation_id: null };
  const upload_id = 'video_adoption_fixture';
  const conversation_id = 'conv_video_adoption';
  const stored_file_id = 'f_video_adoption';
  const sender_message_id = 'message_video_adoption';
  // The row represents a completed upload. Upload transfer and media probing
  // belong to Unit 19 and are deliberately outside this admission test.
  const bytes = Buffer.from('ready video bytes from the upload boundary');
  database.write((sql) =>
    sql.run(
      'INSERT INTO conversation_video_uploads ' +
        '(upload_id,destination_json,filename,byte_count,received_bytes,state,media_type,expires_at,stored_file_id) ' +
        "VALUES (?,?,?,?,?,'ready',?,?,?)",
      [
        upload_id,
        JSON.stringify(destination),
        'demo.mp4',
        bytes.length,
        bytes.length,
        'video/mp4',
        Math.floor(Date.now() / 1000) + 3600,
        stored_file_id,
      ],
    ),
  );
  const source = join(service.root, upload_id);
  writeFileSync(source, bytes);

  const checked = service.preflight(upload_id, destination, sender_message_id);
  expect(service.status(upload_id).state).toBe('admitted');
  const kept = service.adopt(checked, conversation_id);
  expect(kept).toMatchObject({
    conversation_id,
    stored_file_id,
    media_type: 'video/mp4',
    byte_count: bytes.length,
  });
  expect(existsSync(source)).toBe(false);
  expect(readFileSync(kept.absolute_path)).toEqual(bytes);
  expect(readFileSync(kept.absolute_path + '.media-type', 'utf8')).toBe('video/mp4');
  service.finish(checked, 'accepted');
  expect(
    database.read((sql) =>
      sql.get(
        'SELECT state,conversation_id,sender_message_id,outcome FROM conversation_video_uploads WHERE upload_id=?',
        [upload_id],
      ),
    ),
  ).toEqual({ state: 'linked', conversation_id, sender_message_id, outcome: 'accepted' });

  // The sender now names the linked conversation. The accepted retry retains
  // the original reservation and file identity without another temporary upload.
  const retried = service.preflight(
    upload_id,
    { ...destination, expected_conversation_id: conversation_id },
    sender_message_id,
  );
  const kept_again = service.adopt(retried, conversation_id);
  expect(kept_again).toEqual(kept);
  service.finish(retried, 'accepted');
  service.finish(retried, 'refused');
  expect(service.status(upload_id).state).toBe('linked');
  expect(existsSync(source)).toBe(false);
  expect(readFileSync(kept.absolute_path)).toEqual(bytes);

  // Exercise the route's actual factory and content path against the same linked
  // upload. The Unit 14 file service reads the managed bytes named by the piece.
  const files = new ConversationMessageFiles(fixture_path(database));
  const admission = video_send_admission(
    database,
    fixture_path(database),
    files,
    [{ piece: 'video_upload', upload_id }],
    sender_message_id,
    { ...destination, expected_conversation_id: conversation_id },
    { kind: 'ticket', id: destination.id },
    OWNER,
  );
  expect(admission).not.toBeNull();
  expect(await admission!.preflight(conversation_id)).toBeNull();
  const content = await admission!.content(conversation_id);
  expect(content).toEqual([
    new MessageFile({
      stored_file_id,
      media_type: 'video/mp4',
      file_name: 'demo.mp4',
      byte_count: bytes.length,
    }),
  ]);
  expect(Buffer.from(await files.read(conversation_id, stored_file_id))).toEqual(bytes);
  admission!.finish(new PromptDeliveryStarted());
  expect(service.status(upload_id).state).toBe('linked');
  expect(existsSync(source)).toBe(false);
});

// Gap: the video-only path could reject other file types or expose active MIME at the app origin.
test('one streamed upload path accepts unrestricted files under the shared cap with safe MIME', async () => {
  const { MAX_ATTACHMENT_BYTES } =
    await import('../../web/src/lib/conversation/attachmentPolicy.ts');
  const database = fixture();
  const service = new VideoUploadService(database, fixture_path(database), {
    disk_free_bytes: () => 20 * 1024 ** 3,
    probe_video: async () => {
      throw new Error('ffprobe missing or failed');
    },
  });
  const destination = { kind: 'chief' as const, id: 'chief', expected_conversation_id: null };
  for (const filename of ['proof.bin', 'page.html', 'icon.svg', 'invalid.mp4']) {
    const bytes = Buffer.from('<script>arbitrary bytes</script>');
    const upload = service.reserve({ destination, filename, byte_count: bytes.length });
    const status = await service.transfer(
      upload.upload_id,
      (async function* () {
        yield bytes;
      })(),
    );
    expect(status).toMatchObject({ state: 'ready', media_type: 'application/octet-stream' });
    const checked = service.preflight(upload.upload_id, destination, 'any-' + filename);
    const kept = service.adopt(checked, 'all_files');
    expect(readFileSync(kept.absolute_path)).toEqual(bytes);
    service.finish(checked, 'accepted');
  }
  const empty = service.reserve({ destination, filename: 'empty.bin', byte_count: 0 });
  expect((await service.transfer(empty.upload_id, (async function* () {})())).state).toBe('ready');
  expect(
    service.reserve({ destination, filename: 'limit.bin', byte_count: MAX_ATTACHMENT_BYTES }).state,
  ).toBe('reserved');
  expect(() =>
    service.reserve({
      destination,
      filename: 'too-large.bin',
      byte_count: MAX_ATTACHMENT_BYTES + 1,
    }),
  ).toThrow('250 MiB');
});

// Gap: multiple ready images must stay accepted even when their total exceeds native image budgets.
test('multiple streamed rasters keep a bounded native image and readable excess file references', async () => {
  const { MessageImage } = await import('../conversation/message_content.ts');
  const database = fixture();
  const db_path = fixture_path(database);
  const service = new VideoUploadService(database, db_path, {
    disk_free_bytes: () => 20 * 1024 ** 3,
  });
  const destination = { kind: 'ticket' as const, id: 't_images', expected_conversation_id: null };
  const bytes = Buffer.alloc(2 * 1024 ** 2);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bytes);
  const refs: { piece: 'attachment_upload'; upload_id: string; width?: number; height?: number }[] =
    [];
  for (const filename of ['first.png', 'second.png']) {
    const upload = service.reserve({ destination, filename, byte_count: bytes.length });
    // Transfer is proved above. This admission case starts from completed sparse-image metadata.
    writeFileSync(join(service.root, upload.upload_id), bytes);
    database.write((tx) =>
      tx.run(
        "UPDATE conversation_video_uploads SET state='ready',received_bytes=?,media_type='image/png' WHERE upload_id=?",
        [bytes.length, upload.upload_id],
      ),
    );
    refs.push({ piece: 'attachment_upload', upload_id: upload.upload_id });
  }
  // Distinct risk: the size the browser measured is lost before the message is recorded,
  // so the sent picture changes size again when the stored copy loads.
  Object.assign(refs[0]!, { width: 1200, height: 800 });
  const files = new ConversationMessageFiles(db_path);
  const admission = video_send_admission(
    database,
    db_path,
    files,
    [
      {
        piece: 'image',
        data: bytes.toString('base64'),
        media_type: 'image/png',
        file_name: 'legacy.png',
      },
      ...refs,
    ],
    'image-pair',
    destination,
    OWNER,
    OWNER,
  )!;
  await admission.preflight(null);
  const content = await admission.content('image_pair');
  expect(content[0]).toBeInstanceOf(MessageImage);
  expect(content[1]).toBeInstanceOf(MessageFile);
  expect(content[2]).toBeInstanceOf(MessageFile);
  const recorded = message_content_from_stored(message_content_json_entries(content));
  expect(recorded.map((piece) => ('media_size' in piece ? piece.media_size : null))).toEqual([
    null,
    { width: 1200, height: 800 },
    null,
  ]);
  for (const piece of content)
    if ('stored_file_id' in piece)
      expect(Buffer.from(await files.read('image_pair', piece.stored_file_id)).equals(bytes)).toBe(
        true,
      );
  admission.finish(new PromptDeliveryStarted());
});

// A definite refusal permits an edited answer to reuse its retained upload, while acceptance stays pinned.
test('a retained upload can follow edited answer text after definite refusal only', async () => {
  const database = fixture();
  const service = new VideoUploadService(database, fixture_path(database), {
    disk_free_bytes: () => 20 * 1024 ** 3,
  });
  const destination = {
    kind: 'conversation' as const,
    id: 'answer',
    expected_conversation_id: 'answer',
  };
  const bytes = Buffer.from('same answer file');
  const upload = service.reserve({ destination, filename: 'proof.bin', byte_count: bytes.length });
  await service.transfer(
    upload.upload_id,
    (async function* () {
      yield bytes;
    })(),
  );
  const original = service.preflight(
    upload.upload_id,
    destination,
    JSON.stringify(['answer', 'q', 'original text']),
  );
  service.adopt(original, 'answer');
  service.finish(original, 'refused');
  const edited = service.preflight(
    upload.upload_id,
    destination,
    JSON.stringify(['answer', 'q', 'edited text']),
  );
  const kept = service.adopt(edited, 'answer');
  expect(readFileSync(kept.absolute_path)).toEqual(bytes);
  service.finish(edited, 'accepted');
  expect(() => service.preflight(upload.upload_id, destination, 'unrelated-answer')).toThrow(
    'another message',
  );
});
