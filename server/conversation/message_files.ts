/** The file service handed to a backend. Storage implementation belongs to the record unit. */
export interface KeptMessageFile {
    readonly conversation_id: string; readonly stored_file_id: string; readonly media_type: string;
    readonly absolute_path: string; readonly byte_count: number;
}
export interface ConversationMessageFiles {
    keep(conversation_id: string, contents: Uint8Array, options: {media_type: string; sender_message_id?: string | null; piece_index?: number}): Promise<KeptMessageFile>;
    read(conversation_id: string, stored_file_id: string): Promise<Uint8Array>;
    media_type_of(conversation_id: string, stored_file_id: string): Promise<string | null>;
    path_of(conversation_id: string, stored_file_id: string): string;
}
