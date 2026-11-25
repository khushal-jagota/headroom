import { untrack } from 'svelte';
import type { ReviewReplyComposer } from './replyComposer.svelte';

/**
 * How a Review send ends, for the send button. It spins while the send goes, then shows a green
 * tick when it arrived, a red cross when it was refused, an amber question mark when it may not
 * have arrived. A refused or uncertain message returns to the field. Each result leaves after a
 * moment, or sooner when the words change or the next send starts. The Chief box on the empty page
 * and the reply on a message card both use it. Create it while a component starts.
 */
export class ReviewSendOutcome {
  result = $state<'sent' | 'failed' | 'unsure'>();
  note = $state<string>();
  #composer: ReviewReplyComposer;
  #timer: ReturnType<typeof setTimeout> | undefined;
  #unsureText = '';

  constructor(composer: ReviewReplyComposer) {
    this.#composer = composer;
    // A composer shared with another view already holds the send that view saw. Only a newer one counts.
    let seen = composer.accepted;
    $effect(() => {
      const accepted = composer.accepted;
      if (accepted && accepted !== seen) untrack(() => this.#show('sent'));
      seen = accepted;
    });
    $effect(() => {
      const ended = composer.attempts.find(attempt => attempt.status === 'failed' || attempt.status === 'upload-failed'
        || attempt.status === 'connection-failed' || attempt.status === 'uncertain');
      if (!ended || composer.submitting || composer.intakeBusy) return;
      untrack(async () => {
        const unsure = ended.status === 'connection-failed' || ended.status === 'uncertain';
        const reason = ended.note ?? 'The message was not sent.';
        if (!await composer.edit(ended)) return;
        if (unsure) this.#show('unsure', `May not have arrived: ${reason} Sending it again can deliver it twice.`);
        else this.#show('failed', `Not sent: ${reason}`);
      });
    });
    $effect(() => {
      if (this.result === 'unsure' && (this.sending || composer.text !== this.#unsureText)) {
        untrack(() => { this.result = undefined; this.note = undefined; });
      }
    });
    $effect(() => () => clearTimeout(this.#timer));
  }

  /** A send is going: it is being saved, waiting its turn, on its way, or being checked. */
  get sending(): boolean {
    return this.#composer.attempts.some(attempt => attempt.status === 'preparing' || attempt.status === 'waiting'
      || attempt.status === 'sending' || attempt.status === 'checking');
  }

  #show(next: 'sent' | 'failed' | 'unsure', reason?: string): void {
    this.result = next;
    this.note = reason;
    clearTimeout(this.#timer);
    if (next === 'unsure') this.#unsureText = this.#composer.text;
    this.#timer = setTimeout(() => { this.result = undefined; this.note = undefined; }, next === 'sent' ? 1500 : 4000);
  }
}
