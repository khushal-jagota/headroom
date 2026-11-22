<script lang="ts">
  /** The one shell every blocking ask wears: a question, a permission ask, a key request and
   * a proposal. An accent eyebrow says what it needs from the person, the title says what,
   * the content is the thing to decide on, and the footer ends in the one action that
   * commits. Meaning is carried by place, weight and tone, never by a coloured rule.
   *
   * It draws its own raised plane. Docked in the composer's place, the composer's box gives
   * up its own fill and edge for it (data-ask-shell), so the two never nest. */
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  let {
    kind,
    eyebrow,
    context,
    title,
    titleId,
    description = null,
    children,
    footerStart,
    footerEnd,
    replyStrip,
    framed = true,
    ...rest
  }: {
    kind: "question" | "permission" | "credential" | "proposal";
    /** What it needs: "Needs your approval", "Needs your answer", "Needs a key". */
    eyebrow: string;
    /** Quiet facts on the eyebrow's right ("1 of 2", "Implementation · approver You"). */
    context?: Snippet;
    title: string;
    titleId?: string;
    description?: string | null;
    children?: Snippet;
    /** Anything on the left of the footer (none of the asks draws one: the turn is stopped
     *  from the composer's own stop button). */
    footerStart?: Snippet;
    /** The answers, ending in the one primary, on the right. */
    footerEnd?: Snippet;
    /** The composer's one-line reply, under the ask, when it docks in the composer's place. */
    replyStrip?: Snippet;
    framed?: boolean;
  } & Omit<HTMLAttributes<HTMLElement>, "title"> = $props();
</script>

<section class="cv-ask" class:is-framed={framed} data-ask-shell data-ask-kind={kind} {...rest}>
  <div class="cv-ask-body">
    <div class="cv-ask-eyebrow">
      <span class="cv-ask-dot" aria-hidden="true"></span>
      <span class="cv-ask-need">{eyebrow}</span>
      {#if context}<span class="cv-ask-context">{@render context()}</span>{/if}
    </div>
    <h3 class="cv-ask-title" id={titleId}>{title}</h3>
    {#if description}<p class="cv-ask-description">{description}</p>{/if}
    {#if children}<div class="cv-ask-content">{@render children()}</div>{/if}
  </div>
  {#if footerStart || footerEnd}
    <div class="cv-ask-footer">
      {#if footerStart}<div class="cv-ask-footer-start">{@render footerStart()}</div>{/if}
      {#if footerEnd}<div class="cv-ask-footer-end">{@render footerEnd()}</div>{/if}
    </div>
  {/if}
  {#if replyStrip}<div class="cv-ask-reply">{@render replyStrip()}</div>{/if}
</section>

<style>
  .cv-ask {
    min-width: 0;
    width: 100%;
    box-sizing: border-box;
    color: var(--cv-t2);
    font-family: var(--cv-font);
    overflow-wrap: anywhere;
    animation: cv-ask-in var(--cv-dur-base) var(--cv-ease-out);
  }
  .cv-ask.is-framed {
    background: var(--cv-raised);
    border: 1px solid var(--cv-line-strong);
    border-radius: var(--cv-r-xl);
  }
  .cv-ask-body { padding: var(--cv-s4) 20px 0; min-width: 0; }
  .cv-ask-eyebrow {
    display: flex;
    align-items: center;
    gap: var(--cv-s2);
    min-width: 0;
    color: var(--cv-accent);
    font: var(--cv-weight-label) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
  }
  .cv-ask-dot { flex: none; width: 6px; height: 6px; border-radius: var(--cv-r-pill); background: var(--cv-accent); }
  .cv-ask-need { flex: none; }
  .cv-ask-context {
    display: inline-flex;
    align-items: center;
    gap: var(--cv-s1);
    min-width: 0;
    margin-inline-start: auto;
    color: var(--cv-t3);
    font-weight: var(--cv-weight-body);
    font-variant-numeric: tabular-nums;
  }
  .cv-ask-title {
    margin: 6px 0 0;
    color: var(--cv-t1);
    font: var(--cv-weight-title) 16px/24px var(--cv-font);
  }
  .cv-ask-description {
    margin: var(--cv-s1) 0 0;
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-ui)/22px var(--cv-font);
  }
  .cv-ask-content { margin-block-start: var(--cv-s4); min-width: 0; }
  /* The ghost on the left lines its words up with the body's 20px edge (8 + its 12). */
  .cv-ask-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--cv-s2);
    padding: var(--cv-s4) var(--cv-s4) var(--cv-s4) var(--cv-s2);
  }
  .cv-ask-footer-start { display: flex; align-items: center; gap: var(--cv-s2); }
  .cv-ask-footer-end {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: var(--cv-s2);
    min-width: 0;
    margin-inline-start: auto;
  }
  .cv-ask-reply { padding: 0 var(--cv-s2) var(--cv-s2); }
  @keyframes cv-ask-in { from { opacity: 0; transform: translateY(4px); } }
  @media (prefers-reduced-motion: reduce) { .cv-ask { animation: none; } }
  @media (max-width: 560px) {
    .cv-ask-body { padding-inline: var(--cv-s4); }
    .cv-ask-footer { padding-inline: var(--cv-s1) var(--cv-s3); }
  }
</style>
