<script lang="ts">
  import type { Snippet } from "svelte";

  /* THE button (Direction v2, Outcome 5). Four variants: primary (inverse), secondary
   * (13% fill), outline (a page header's own actions: a 1px edge, no fill) and ghost (no
   * fill until hovered). The older names still work and land on
   * the same two classes: quiet = secondary, pill = ghost. The look lives in app.css,
   * "Buttons". */
  type Variant = "primary" | "secondary" | "outline" | "ghost";
  type LegacyVariant = "quiet" | "pill";
  const legacy: Record<LegacyVariant, Variant> = { quiet: "secondary", pill: "ghost" };

  let {
    variant = "secondary",
    href,
    disabled = false,
    onclick,
    class: extraClass = "",
    children,
    ...rest
  }: {
    variant?: Variant | LegacyVariant;
    href?: string;
    disabled?: boolean;
    onclick?: (event: MouseEvent) => void;
    class?: string;
    children?: Snippet;
    [key: string]: unknown;
  } = $props();

  let resolved = $derived<Variant>(variant in legacy ? legacy[variant as LegacyVariant] : (variant as Variant));
  let buttonClass = $derived(
    `button button--${resolved}${extraClass ? ` ${extraClass}` : ""}`
  );
</script>

{#if href !== undefined}
  <a class={buttonClass} {href} {...rest}>{@render children?.()}</a>
{:else}
  <button type="button" class={buttonClass} {disabled} {onclick} {...rest}>
    {@render children?.()}
  </button>
{/if}
