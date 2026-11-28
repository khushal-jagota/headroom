<script lang="ts">
  import { getContext } from "svelte";
  import Button from "./Button.svelte";
  import { REVIEW_PASS_CONTEXT, type ReviewPassContext } from "../lib/review/pass";

  const pass = getContext<ReviewPassContext | undefined>(REVIEW_PASS_CONTEXT);
  const action = $derived(pass?.action() ?? null);
</script>

{#if action}<Button variant="ghost" data-review-pass disabled={action.disabled} onclick={action.run}>{action.label}</Button>{/if}
