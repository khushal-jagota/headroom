import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { video_send_admission } from '../conversation/video_send_admission.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { MessageFile } from '../conversation/message_content.ts';
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
