<script lang="ts">
  /** The chevron after every Details action: it points right while the backend's words are
   *  hidden and turns down when they open. The failed turn's line and the undelivered
   *  bubble draw the same one. */
  let { open }: { open: boolean } = $props();
</script>

<svg class="c2-details-chevron" class:is-open={open} viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>

<style>
  .c2-details-chevron {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out);
  }
  .c2-details-chevron.is-open { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) { .c2-details-chevron { transition: none; } }
</style>
