import { connectionStatus } from '../changeStream';
import { fateSentence } from '../conversation/composer';
import { readComposerDraft, writeComposerDraft } from '../conversation/composerDraft';
import { mintOutgoingMessage, theServerRefusedTheSend } from '../conversation/outgoing';
import { ownerSendPath, postOwnerSend, readOwnerSend, type SendOwner } from '../conversation/ownerSend';
import { MAX_CONVERSATION_MESSAGE_FILE_BYTES, pendingConversationFileBytes, pendingFilesAsPieces, restoredPendingFiles, type PendingConversationFile } from '../conversation/pendingFiles';
import { MAX_CONVERSATION_MESSAGE_IMAGE_BYTES, pendingConversationImageBytes, pendingImagesAsPieces, releasePendingImages, restoredPendingImages, type PendingConversationImage } from '../conversation/pendingImages';
import { clearAttachmentDraft, openAttachmentDraft, pendingUploadsAsPieces, publishAttachmentDraft, restoredPendingUploads } from '../conversation/videoUploads/uploads';
import type { PendingAttachment, UploadDestination } from '../conversation/videoUploads/contracts';
import { messageContentText, type DeliveredMessage, type SentMessagePiece } from '../conversation/wire';
import { readReviewDraft, saveReviewDraft } from './messages';
import { recallReviewAttempts, rememberReviewAttempts, type ReviewReplyAttempt } from './sendAttempts';

type ReplyStatus = 'checking' | 'sending' | 'connection-failed' | 'failed' | 'uncertain';
export type ReviewOutgoingReply = ReviewReplyAttempt & { status: ReplyStatus; note: string | null };

type Options = {
  draftKey: string;
  attachmentDraftId: string;
  owner: SendOwner;
  destination: () => string | null | Promise<string | null>;
  attemptKey: string;
  attemptMetadataKey: string;
  legacyConversationId?: string;
};

function statusFor(delivery: DeliveredMessage): Pick<ReviewOutgoingReply, 'status' | 'note'> {
  if (delivery.fate === 'uncertain') {
    return { status: 'uncertain', note: 'The agent did not confirm receipt.' };
  }
  return { status: 'failed', note: fateSentence(delivery) ?? 'The reply was not sent.' };
}

// A card and its bar beneath an open artifact edit one draft and send from one list.
const mounted = new Map<string, { composer: ReviewReplyComposer; mounts: number }>();

export function reviewReplyComposer(options: Options): ReviewReplyComposer {
  let entry = mounted.get(options.draftKey);
  if (!entry) {
    entry = { composer: new ReviewReplyComposer(options), mounts: 0 };
    mounted.set(options.draftKey, entry);
  }
  return entry.composer;
}

export class ReviewReplyComposer {
  #options: Options;
  #key: string;
  #mountsAbort: AbortController | null = null;
  #unsubscribe: (() => void) | null = null;
  #checking = new Set<string>();
  #text = $state('');
  #files = $state.raw<PendingConversationFile[]>([]);
  #images = $state.raw<PendingConversationImage[]>([]);
  #uploads = $state.raw<PendingAttachment[]>([]);
  uploadDestination: UploadDestination = $state({ kind: 'chief', id: 'chief', expected_conversation_id: null });
  attachmentReady = $state(false);
  #attachmentWrite = Promise.resolve(true);
  #attemptWrite = Promise.resolve(true);
  #nextAttachmentId = 0;
  attempts = $state.raw<ReviewOutgoingReply[]>([]);
  ready = $state(false);
  submitting = $state(false);
  intakeBusy = $state(false);
  error = $state<unknown>(null);
  accepted = $state<{ through: number | undefined; fate: 'started' | 'injected' | 'queued' } | null>(null);

  constructor(options: Options) {
    this.#options = options;
    this.#key = options.draftKey;
    this.#text = readComposerDraft(this.#key);
    this.uploadDestination = { ...options.owner, expected_conversation_id: options.legacyConversationId ?? null };
  }

  get draftKey() { return this.#key; }
  get text() { return this.#text; }
  set text(value: string) {
    this.#text = value;
    writeComposerDraft(this.#key, value);
  }
  get files() { return this.#files; }
  set files(value: PendingConversationFile[]) {
    this.#files = value;
    void this.#saveAttachments();
  }
  get images() { return this.#images; }
  set images(value: PendingConversationImage[]) {
    this.#images = value;
    void this.#saveAttachments();
  }
  get uploads() { return this.#uploads; }
  set uploads(value: PendingAttachment[]) {
    if (this.#uploads === value) return;
    this.#uploads = value;
    if (this.ready) void this.#saveAttachments();
  }
  get hasDraft() { return Boolean(this.text.trim() || this.files.length || this.images.length || this.uploads.length); }

  mount(): () => void {
    const entry = mounted.get(this.#key)!;
    entry.mounts += 1;
    if (entry.mounts === 1) {
      const scope = this.#mountsAbort = new AbortController();
      if (!this.ready) void this.#restore(scope.signal);
      this.#unsubscribe = connectionStatus.subscribe(state => {
        if (state === 'connected') void this.reconcile(scope.signal);
      });
    }
    return () => {
      entry.mounts -= 1;
      if (entry.mounts > 0) return;
      this.#mountsAbort?.abort();
      this.#unsubscribe?.();
      this.#disposeIfIdle();
    };
  }

  #disposeIfIdle(): void {
    if (mounted.get(this.#key)?.mounts !== 0 || this.submitting) return;
    releasePendingImages(this.#images);
    mounted.delete(this.#key);
  }

  #attachments() {
    return [...pendingImagesAsPieces(this.images), ...pendingFilesAsPieces(this.files), ...pendingUploadsAsPieces(this.uploads)];
  }

  #content(): SentMessagePiece[] {
    const text = this.text.trim();
    return [...(text ? [{ piece: 'text' as const, text }] : []), ...this.#attachments()];
  }

  #saveAttachments(): Promise<boolean> {
    const content = this.#attachments();
    this.#attachmentWrite = this.#attachmentWrite.then(async () => {
      const saved = await saveReviewDraft(this.#options.attachmentDraftId, content);
      if (!saved) this.error = new Error('The attachments could not be saved in this browser.');
      return saved;
    }).catch(problem => { this.error = problem; return false; });
    return this.#attachmentWrite;
  }

  #restoreAttachments(pieces: SentMessagePiece[]) {
    const images = restoredPendingImages(pieces, this.#nextAttachmentId);
    const files = restoredPendingFiles(pieces, images.nextId);
    this.#nextAttachmentId = files.nextId;
    releasePendingImages(this.#images);
    this.#images = images.images;
    this.#files = files.files;
  }

  async #restore(signal: AbortSignal): Promise<void> {
    try {
      const openedText = this.text;
      const [attachments, attempts, destination] = await Promise.allSettled([
        readReviewDraft(this.#options.attachmentDraftId),
        recallReviewAttempts(this.#options.attemptKey, this.#options.attemptMetadataKey, this.#options.legacyConversationId),
        this.#options.destination()
      ]);
      if (signal.aborted) return;
      if (destination.status === 'fulfilled') {
        this.uploadDestination = { ...this.#options.owner, expected_conversation_id: destination.value };
        this.attachmentReady = true;
      } else this.error = destination.reason;
      if (attachments.status === 'fulfilled') {
        this.#restoreAttachments(attachments.value);
        this.#setUploads(restoredPendingUploads(attachments.value, this.uploadDestination, openAttachmentDraft(this.draftKey)));
      }
      else this.error = attachments.reason;
      if (attempts.status === 'rejected') throw attempts.reason;
      this.attempts = attempts.value.map(attempt => ({
        ...attempt,
        status: attempt.localRefusal ? 'failed' : 'checking',
        note: attempt.localRefusal ?? null
      }));
      // Older Review stored the very same text and attachments as both draft and send.
      // Preserve anything that differs, including a draft typed on this mount.
      if (openedText === this.text && attempts.value.some(attempt => JSON.stringify(attempt.message.content) === JSON.stringify(this.#content()))) {
        this.#clearDraft();
        await this.#attachmentWrite;
      }
    } catch (problem) {
      if (!signal.aborted) this.error = problem;
    } finally {
      if (!signal.aborted) this.ready = true;
    }
    await this.reconcile(signal);
  }

  #setUploads(uploads: PendingAttachment[]) {
    this.#uploads = uploads;
    publishAttachmentDraft(this.draftKey, uploads);
  }

  #clearDraft() {
    this.text = '';
    releasePendingImages(this.#images);
    this.#images = [];
    this.#files = [];
    clearAttachmentDraft(this.draftKey);
    this.#uploads = [];
    void this.#saveAttachments();
  }

  #store(attempts: ReviewOutgoingReply[]): Promise<boolean> {
    const contents = attempts.map(({ message, conversationId, localRefusal }) => ({ message, conversationId, localRefusal }));
    this.#attemptWrite = this.#attemptWrite.then(() => rememberReviewAttempts(
      this.#options.attemptKey, this.#options.attemptMetadataKey, contents
    ))
      .catch(problem => { this.error = problem; return false; });
    return this.#attemptWrite;
  }

  #update(id: string, update: Pick<ReviewOutgoingReply, 'status' | 'note' | 'localRefusal'>) {
    this.attempts = this.attempts.map(attempt => attempt.message.messageId === id ? { ...attempt, ...update } : attempt);
  }

  #remove(id: string) {
    this.attempts = this.attempts.filter(attempt => attempt.message.messageId !== id);
    return this.#store(this.attempts);
  }

  async #apply(attempt: ReviewReplyAttempt, delivery: DeliveredMessage): Promise<void> {
    const id = attempt.message.messageId;
    if (delivery.fate === 'started' || delivery.fate === 'injected' || delivery.fate === 'queued') {
      this.accepted = { through: attempt.message.ownerReadThroughSequence, fate: delivery.fate };
      if (!this.legacyDestination && !this.uploads.length) this.#updateUploadDestination(delivery.conversation_id);
      await this.#remove(id);
      return;
    }
    this.#update(id, statusFor(delivery));
    if (delivery.fate !== 'uncertain' && delivery.conversation_id !== null && attempt.conversationId !== delivery.conversation_id) {
      // A confirmed failure gets a new message ID on retry. Keep that retry with the
      // first send's destination even when it was unknown before the send.
      this.attempts = this.attempts.map(entry => entry.message.messageId === id
        ? { ...entry, conversationId: delivery.conversation_id } : entry);
      await this.#store(this.attempts);
    }
  }

  async #check(attempt: ReviewOutgoingReply, signal?: AbortSignal): Promise<void> {
    const id = attempt.message.messageId;
    if (this.#checking.has(id)) return;
    this.#checking.add(id);
    try {
      const delivery = await readOwnerSend(this.#options.owner, id, signal);
      if (signal?.aborted) return;
      if (delivery) await this.#apply(attempt, delivery);
      else this.#noteMissingResult(attempt);
    } catch {
      if (signal?.aborted) return;
      this.#noteMissingResult(attempt);
    } finally {
      this.#checking.delete(id);
    }
  }

  #noteMissingResult(attempt: ReviewOutgoingReply): void {
    if (attempt.status === 'uncertain') return;
    this.#update(attempt.message.messageId, { status: 'connection-failed', note: 'Panels did not confirm the send.' });
  }

  async reconcile(signal?: AbortSignal): Promise<void> {
    if (!this.ready || this.submitting) return;
    await Promise.all(this.attempts
      .filter(attempt => attempt.status === 'checking' || attempt.status === 'connection-failed' || attempt.status === 'uncertain')
      .map(attempt => this.#check(attempt, signal)));
  }

  async #post(attempt: ReviewOutgoingReply, retryingConnection = false): Promise<void> {
    const { message, conversationId } = attempt;
    const id = message.messageId;
    const bound = message.ownerReadThroughSequence;
    try {
      const delivery = await postOwnerSend(ownerSendPath(this.#options.owner), {
        conversation_id: conversationId, content: message.content, sender_label: message.senderLabel, mode: message.mode,
        sender_message_id: id, sent_at_unix_milliseconds: message.sentAtUnixMilliseconds,
        ...(bound === undefined ? {} : { owner_read_through_sequence: bound }),
        ...(message.reconcilesSequence === undefined ? {} : { reconciles_sequence: message.reconcilesSequence })
      });
      await this.#apply(attempt, delivery);
    } catch (problem) {
      if (!retryingConnection && theServerRefusedTheSend(problem)) {
        const note = (problem as Error).message;
        this.#update(id, { status: 'failed', note, localRefusal: note });
        await this.#store(this.attempts);
      } else {
        this.#update(id, { status: 'checking', note: null });
        await this.#check(attempt);
      }
    }
  }

  get legacyDestination() { return this.#options.legacyConversationId; }

  #updateUploadDestination(conversationId: string | null) {
    this.uploadDestination = { ...this.#options.owner, expected_conversation_id: conversationId };
    this.attachmentReady = true;
  }

  async send(through?: number): Promise<void> {
    if (this.submitting || this.intakeBusy || !this.ready || !this.hasDraft) return;
    this.submitting = true;
    this.error = null;
    this.accepted = null;
    const text = this.text;
    const images = this.images;
    const files = this.files;
    const uploads = this.uploads;
    const content = this.#content();
    try {
      const conversationId = uploads.length ? uploads[0].destination.expected_conversation_id : await this.#options.destination();
      if (!uploads.length) this.#updateUploadDestination(conversationId);
      const message = mintOutgoingMessage({ content, senderLabel: 'owner', mode: 'queue' });
      if (through !== undefined) message.ownerReadThroughSequence = through;
      const entry: ReviewOutgoingReply = { conversationId, message, status: 'sending', note: null };
      this.attempts = [...this.attempts, entry];
      if (!await this.#store(this.attempts)) {
        await this.#remove(message.messageId);
        throw new Error('The reply could not be saved in this browser. Your draft is kept.');
      }
      if (this.text === text && this.images === images && this.files === files && this.uploads === uploads) {
        this.#clearDraft();
      }
      await this.#post(entry);
    } catch (problem) {
      this.error = problem;
    } finally {
      this.submitting = false;
      this.#disposeIfIdle();
    }
  }

  async retry(attempt: ReviewOutgoingReply): Promise<void> {
    if (this.submitting || this.#checking.has(attempt.message.messageId)) return;
    if (attempt.status !== 'failed' && attempt.status !== 'connection-failed') return;
    this.submitting = true;
    this.error = null;
    const original = this.attempts;
    try {
      const message = attempt.status === 'failed'
        ? {
            ...mintOutgoingMessage({ content: attempt.message.content, senderLabel: attempt.message.senderLabel, mode: attempt.message.mode }),
            ownerReadThroughSequence: attempt.message.ownerReadThroughSequence
          }
        : attempt.message;
      const next: ReviewOutgoingReply = { conversationId: attempt.conversationId, message, status: 'sending', note: null };
      this.attempts = this.attempts.map(candidate => candidate.message.messageId === attempt.message.messageId ? next : candidate);
      if (!await this.#store(this.attempts)) {
        this.attempts = original;
        throw new Error('The reply could not be saved in this browser.');
      }
      await this.#post(next, attempt.status === 'connection-failed');
    } catch (problem) {
      this.error = problem;
    } finally {
      this.submitting = false;
      this.#disposeIfIdle();
    }
  }

  async edit(attempt: ReviewOutgoingReply): Promise<boolean> {
    if (this.submitting || this.intakeBusy || attempt.status !== 'failed') return false;
    const nextId = Math.max(this.#nextAttachmentId, ...[...this.#images, ...this.#files].map(file => file.id + 1));
    const restoredImages = restoredPendingImages(attempt.message.content, nextId);
    const restoredFiles = restoredPendingFiles(attempt.message.content, restoredImages.nextId);
    const images = [...this.images, ...restoredImages.images];
    const files = [...this.files, ...restoredFiles.files];
    if (pendingConversationImageBytes(images) > MAX_CONVERSATION_MESSAGE_IMAGE_BYTES
      || pendingConversationFileBytes(files) > MAX_CONVERSATION_MESSAGE_FILE_BYTES) {
      this.error = new Error('Send or remove attachments from your current draft before adding this reply.');
      return false;
    }
    this.submitting = true;
    this.error = null;
    try {
      this.text = [messageContentText(attempt.message.content), this.text].filter(Boolean).join('\n\n');
      this.#images = images;
      this.#files = files;
      this.#nextAttachmentId = restoredFiles.nextId;
      const attemptDestination: UploadDestination = { ...this.#options.owner, expected_conversation_id: attempt.conversationId };
      this.#setUploads(restoredPendingUploads(attempt.message.content, attemptDestination, this.uploads));
      if (!await this.#saveAttachments()) return false;
      await this.#remove(attempt.message.messageId);
      return true;
    } finally {
      this.submitting = false;
      this.#disposeIfIdle();
    }
  }
}
