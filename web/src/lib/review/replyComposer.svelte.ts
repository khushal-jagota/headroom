import { untrack } from 'svelte';
import { connectionStatus } from '../changeStream';
import { fateSentence } from '../conversation/composer';
import { readComposerDraft, writeComposerDraft } from '../conversation/composerDraft';
import { mintOutgoingMessage, theServerRefusedTheSend } from '../conversation/outgoing';
import { ownerSendPath, postOwnerSend, readOwnerSend, type SendOwner } from '../conversation/ownerSend';
import { MAX_CONVERSATION_MESSAGE_FILE_BYTES, pendingConversationFileBytes, pendingFilesAsPieces, restoredPendingFiles, type PendingConversationFile } from '../conversation/pendingFiles';
import { MAX_CONVERSATION_MESSAGE_IMAGE_BYTES, pendingConversationImageBytes, pendingImagesAsPieces, releasePendingImages, restoredPendingImages, type PendingConversationImage } from '../conversation/pendingImages';
import { attachmentTransfersFor, clearAttachmentDraft, openAttachmentDraft, pendingUploadsAsPieces, publishAttachmentDraft, restoredPendingUploads } from '../conversation/videoUploads/uploads';
import { videoUploadAdapter } from '../conversation/videoUploads/adapter';
import type { PendingAttachment, UploadDestination } from '../conversation/videoUploads/contracts';
import { messageContentText, type DeliveredMessage, type SentMessagePiece } from '../conversation/wire';
import { readReviewDraft, saveReviewDraft } from './messages';
import { recallReviewAttempts, rememberReviewAttempts, type ReviewReplyAttempt } from './sendAttempts';

type ReplyStatus = 'preparing' | 'uploading' | 'upload-failed' | 'waiting' | 'checking' | 'sending'
  | 'connection-failed' | 'failed' | 'uncertain' | 'queued' | 'sent';
export type ReviewOutgoingReply = ReviewReplyAttempt & {
  status: ReplyStatus;
  note: string | null;
  /** The live transfer stays observable after its token enters the immutable POST. */
  postedUploads?: PendingAttachment[];
};

function isAccepted(attempt: ReviewOutgoingReply): boolean {
  return attempt.status === 'queued' || attempt.status === 'sent';
}

function uploadFailed(upload: PendingAttachment): boolean {
  return upload.phase === 'failed' || upload.phase === 'interrupted';
}

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
  #watchUploads: (() => void) | null = null;
  #text = $state('');
  #files = $state.raw<PendingConversationFile[]>([]);
  #images = $state.raw<PendingConversationImage[]>([]);
  #uploads = $state.raw<PendingAttachment[]>([]);
  uploadDestination: UploadDestination = $state({ kind: 'chief', id: 'chief', expected_conversation_id: null });
  attachmentReady = $state(false);
  #attachmentWrite = Promise.resolve(true);
  #attemptWrite = Promise.resolve(true);
  #nextAttachmentId = 0;
  attempts = $state<ReviewOutgoingReply[]>([]);
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
  get hasUnsettled() { return this.attempts.some(attempt => !isAccepted(attempt)); }
  get canAttach() {
    return this.attachmentReady && (this.uploadDestination.expected_conversation_id !== null || !this.attempts.some(attempt =>
      attempt.status === 'preparing' || attempt.status === 'waiting' || attempt.status === 'uploading' || attempt.status === 'sending'));
  }
  get #transfers() { return attachmentTransfersFor(this.draftKey, videoUploadAdapter); }
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
      if (!this.#watchUploads) this.#watchUploads = $effect.root(() => {
        $effect(() => {
          for (const attempt of this.attempts) {
            if (!attempt.pendingUploads) continue;
            for (const upload of attempt.pendingUploads) {
              // File transfer ownership stays with the draft registry; the
              // same reactive handles now belong to this outgoing message.
              void upload.phase;
              void upload.uploadId;
            }
          }
          untrack(() => { void this.#pump(); });
        });
      });
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
    if (mounted.get(this.#key)?.mounts !== 0 || this.submitting || this.attempts.some(attempt =>
      attempt.status === 'preparing' || attempt.status === 'uploading' || attempt.status === 'waiting')) return;
    this.#watchUploads?.();
    releasePendingImages(this.#images);
    mounted.delete(this.#key);
  }

  #attachments() {
    return [...pendingImagesAsPieces(this.images), ...pendingFilesAsPieces(this.files), ...pendingUploadsAsPieces(this.uploads)];
  }

  #content(attachments = this.#attachments()): SentMessagePiece[] {
    const text = this.text.trim();
    return [...(text ? [{ piece: 'text' as const, text }] : []), ...attachments];
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
        status: attempt.pendingUploads ? 'uploading' : attempt.localRefusal ? 'failed' : 'checking',
        note: attempt.localRefusal ?? null
      }));
      // Older Review stored the very same text and attachments as both draft and send.
      // Preserve anything that differs, including a draft typed on this mount.
      const savedDraft = attachments.status === 'fulfilled' ? this.#content(attachments.value) : null;
      const unrecordedUpload = this.uploads.some(upload => !savedDraft?.some(piece =>
        (piece.piece === 'attachment_upload' || piece.piece === 'video_upload') && piece.upload_id === upload.uploadId));
      if (savedDraft && !unrecordedUpload && openedText === this.text
        && attempts.value.some(attempt => attempt.pendingUploads === undefined
          && JSON.stringify(attempt.message.content) === JSON.stringify(savedDraft))) {
        this.text = '';
        this.#clearAttachments();
        await this.#attachmentWrite;
      }
    } catch (problem) {
      if (!signal.aborted) this.error = problem;
    } finally {
      if (!signal.aborted) this.ready = true;
    }
    if (signal.aborted) return;
    for (const attempt of this.attempts) {
      for (const attachment of attempt.pendingUploads ?? []) {
        if (attachment.phase === 'validating') void this.#transfers.validate(attachment);
      }
    }
    void this.#pump();
    await this.reconcile(signal);
  }

  #setUploads(uploads: PendingAttachment[]) {
    this.#uploads = uploads;
    publishAttachmentDraft(this.draftKey, uploads);
  }

  #clearAttachments(moveUploads = false) {
    releasePendingImages(this.#images);
    this.#images = [];
    this.#files = [];
    if (moveUploads) this.#setUploads([]);
    else {
      clearAttachmentDraft(this.draftKey);
      this.#uploads = [];
      void this.#saveAttachments();
    }
  }

  #store(attempts: ReviewOutgoingReply[]): Promise<boolean> {
    const contents = attempts.filter(attempt => !isAccepted(attempt)).map(({ message, conversationId, localRefusal, pendingUploads }) => ({
      message, conversationId, localRefusal,
      pendingUploads: pendingUploads?.map(upload => ({ ...upload }))
    }));
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
      if (this.#options.legacyConversationId === undefined && !this.uploads.length) this.#updateUploadDestination(delivery.conversation_id);
      this.#update(id, {
        status: delivery.fate === 'queued' ? 'queued' : 'sent',
        note: delivery.fate === 'queued' ? 'Queued' : delivery.fate === 'injected' ? 'Sent to the running turn' : 'Sent'
      });
      await this.#store(this.attempts);
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

  #updateUploadDestination(conversationId: string | null) {
    this.uploadDestination = { ...this.#options.owner, expected_conversation_id: conversationId };
    this.attachmentReady = true;
  }

  async send(through?: number): Promise<void> {
    if (!this.ready || !this.hasDraft) return;
    this.error = null;
    const uploads = this.uploads;
    const originalText = this.text;
    const content = this.#content([...pendingImagesAsPieces(this.images), ...pendingFilesAsPieces(this.files)]);
    const conversationId = uploads.length ? uploads[0].destination.expected_conversation_id : this.uploadDestination.expected_conversation_id;
    const message = mintOutgoingMessage({ content, senderLabel: 'owner', mode: 'queue' });
    if (through !== undefined) message.ownerReadThroughSequence = through;
    const entry: ReviewOutgoingReply = { conversationId, message, pendingUploads: uploads, status: 'preparing', note: null };

    this.attempts = [...this.attempts, entry];
    // Empty the field now. Keep its stored words until the outgoing snapshot has
    // a durable copy, and never erase a newer draft saved during that write.
    this.#text = '';
    this.#clearAttachments(true);
    if (!await this.#store(this.attempts)) {
      this.#update(message.messageId, { status: 'upload-failed', note: 'The message could not be saved in this browser.' });
      this.#disposeIfIdle();
      return;
    }
    if (this.text === '' && readComposerDraft(this.draftKey) === originalText) writeComposerDraft(this.draftKey, '');
    void this.#saveAttachments();
    this.#update(message.messageId, { status: uploads.length > 0 ? 'uploading' : 'waiting', note: null });
    await this.#pump();
  }

  async #pump(): Promise<void> {
    if (this.submitting || !this.ready) return;
    const pending = this.attempts.find(attempt => attempt.status === 'preparing'
      || attempt.status === 'waiting' || attempt.status === 'uploading');
    if (!pending || pending.status === 'preparing' || !pending.pendingUploads) return;
    const id = pending.message.messageId;
    const failure = pending.pendingUploads.find(uploadFailed);
    if (failure) {
      this.#update(id, { status: 'upload-failed', note: failure.error ?? 'The attachment upload could not finish.' });
      await this.#store(this.attempts);
      this.#disposeIfIdle();
      void this.#pump();
      return;
    }
    if (pending.pendingUploads.some(upload => upload.phase !== 'ready' || !upload.uploadId)) {
      // Keep the latest upload tokens for reload recovery without saving file bytes.
      void this.#store(this.attempts);
      return;
    }

    this.submitting = true;
    const message = { ...pending.message, content: [...pending.message.content, ...pendingUploadsAsPieces(pending.pendingUploads)] };
    const outgoing: ReviewOutgoingReply = {
      conversationId: pending.conversationId, message, postedUploads: pending.pendingUploads,
      status: 'sending', note: null
    };
    this.attempts = this.attempts.map(attempt => attempt.message.messageId === id ? outgoing : attempt);
    try {
      if (!await this.#store(this.attempts)) {
        this.attempts = this.attempts.map(attempt => attempt.message.messageId === id
          ? { ...pending, status: 'upload-failed', note: 'The message could not be saved in this browser.' } : attempt);
        return;
      }
      await this.#post(outgoing);
    } finally {
      this.submitting = false;
      this.#disposeIfIdle();
      void this.#pump();
    }
  }

  canRetry(attempt: ReviewOutgoingReply): boolean {
    return attempt.status === 'failed' || attempt.status === 'connection-failed'
      || attempt.status === 'upload-failed' && Boolean(attempt.pendingUploads?.every(upload => !uploadFailed(upload) || upload.file));
  }

  async retry(attempt: ReviewOutgoingReply): Promise<void> {
    if (this.submitting || this.#checking.has(attempt.message.messageId)) return;
    if (!this.canRetry(attempt)) return;
    if (attempt.status === 'upload-failed') {
      this.#update(attempt.message.messageId, { status: 'uploading', note: null });
      await Promise.all((attempt.pendingUploads ?? []).filter(uploadFailed).map(upload => this.#transfers.start(upload)));
      void this.#pump();
      return;
    }
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
      const next: ReviewOutgoingReply = { conversationId: attempt.conversationId, message, postedUploads: attempt.postedUploads, status: 'sending', note: null };
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
      void this.#pump();
    }
  }

  async edit(attempt: ReviewOutgoingReply): Promise<boolean> {
    if (this.submitting || this.intakeBusy || (attempt.status !== 'failed' && attempt.status !== 'upload-failed')) return false;
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
      const uploads = attempt.pendingUploads
        ? [...this.uploads, ...attempt.pendingUploads.map((upload, index) => ({
            ...upload, id: Math.max(0, ...this.uploads.map(item => item.id)) + index + 1
          }))]
        : restoredPendingUploads(attempt.message.content, attemptDestination, this.uploads);
      this.#setUploads(uploads);
      if (!await this.#saveAttachments()) return false;
      await this.#remove(attempt.message.messageId);
      return true;
    } finally {
      this.submitting = false;
      this.#disposeIfIdle();
      void this.#pump();
    }
  }
}
