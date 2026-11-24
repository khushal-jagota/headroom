<script lang="ts">
  import { splitSkillFrontmatter } from "../../lib/settings/skillFrontmatter";
  import MarkdownBlock from "../MarkdownBlock.svelte";

  /* A skill's instructions, rendered as Markdown on the recessed surface. Copies of the
     skill's frontmatter at the top of the body are not shown. */
  let { markdown, id }: { markdown: string; id?: string } = $props();
  let body = $derived(splitSkillFrontmatter(markdown).body);
</script>

<div class="settings-well" {id} data-settings-well>
  <MarkdownBlock text={body} quiet="No instructions." />
</div>

<style>
  .settings-well { position:relative; margin-top:var(--space-3); padding:var(--space-4) var(--space-5); background:var(--surface-recessed); border-radius:var(--radius-lg); box-shadow:inset 0 1px 3px rgb(0 0 0 / 45%); max-height:520px; overflow-y:auto; font-size:var(--type-body); line-height:1.6; color:var(--tone-primary); }
  .settings-well :global(.markdown-block > :first-child) { margin-top:0; }
  .settings-well :global(:is(h1, h2, h3, h4)) { margin:var(--space-5) 0 var(--space-2); font-size:var(--type-page-section); font-weight:var(--weight-page-section); color:var(--tone-strong); line-height:1.35; }
  .settings-well :global(:is(h3, h4)) { font-size:var(--type-body); }
  .settings-well :global(:is(p, ul, ol)) { margin:0 0 var(--space-3); }
  .settings-well :global(:is(ul, ol)) { padding-left:var(--space-5); }
  .settings-well :global(li + li) { margin-top:var(--space-1); }
  .settings-well :global(pre) { margin:0 0 var(--space-3); padding:var(--space-3); border-radius:var(--radius-md); background:var(--surface-code); overflow-x:auto; font:var(--type-code)/1.6 var(--font-mono); }
  .settings-well :global(strong) { color:var(--tone-strong); }
  .settings-well :global(a) { color:var(--tone-strong); text-decoration:underline; text-underline-offset:2px; }
</style>
