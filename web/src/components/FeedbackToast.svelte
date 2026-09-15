<script lang="ts">
  import { onMount } from "svelte";

  let {
    message,
    actionLabel = null,
    actionHref = null,
    onAction,
    duration = 4000,
    onDone
  }: {
    message: string;
    actionLabel?: string | null;
    actionHref?: string | null;
    onAction?: () => void;
    duration?: number;
    onDone: () => void;
  } = $props();

  onMount(() => {
    const timer = window.setTimeout(() => onDone(), duration);
    return () => window.clearTimeout(timer);
  });
</script>

<div class="fb-toast" role="status" aria-live="polite" data-feedback-toast>
  <span>{message}{#if actionLabel && actionHref}{" · "}<a href={actionHref} onclick={onDone}>{actionLabel}</a>{:else if actionLabel && onAction}{" · "}<button type="button" onclick={onAction}>{actionLabel}</button>{/if}</span>
</div>
