/** General attachments can be empty. Keep every existing upload and its admission state. */
import type { SchemaStep } from './contracts.ts';

export const empty_attachments_step: SchemaStep = {
  revision: 'ts_0011_empty_attachments',
  previous_revision: 'ts_0010_answer_receipt_index',
  apply(tx) {
    tx.run(
      'CREATE TABLE conversation_video_uploads_next (upload_id TEXT PRIMARY KEY,destination_json TEXT NOT NULL,filename TEXT NOT NULL,byte_count INTEGER NOT NULL CHECK(byte_count >= 0),received_bytes INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL,media_type TEXT,expires_at INTEGER NOT NULL,error TEXT,sender_message_id TEXT,conversation_id TEXT,stored_file_id TEXT NOT NULL,outcome TEXT)',
    );
    tx.run(
      'INSERT INTO conversation_video_uploads_next (upload_id,destination_json,filename,byte_count,received_bytes,state,media_type,expires_at,error,sender_message_id,conversation_id,stored_file_id,outcome) SELECT upload_id,destination_json,filename,byte_count,received_bytes,state,media_type,expires_at,error,sender_message_id,conversation_id,stored_file_id,outcome FROM conversation_video_uploads',
    );
    tx.run('DROP TABLE conversation_video_uploads');
    tx.run('ALTER TABLE conversation_video_uploads_next RENAME TO conversation_video_uploads');
  },
};
