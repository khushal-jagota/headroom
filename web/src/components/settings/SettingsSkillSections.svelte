<script lang="ts">
  import Button from "../Button.svelte";
  import ErrorLine from "../ErrorLine.svelte";
  import SettingsMarkdownWell from "./SettingsMarkdownWell.svelte";
  import SettingsRow from "./SettingsRow.svelte";
  import SettingsSection from "./SettingsSection.svelte";
  import { splitSkillFrontmatter } from "../../lib/settings/skillFrontmatter";
  import type { ManagedSkill, ManagedSkillField } from "../../lib/types";

  /* An agent's skill: its name, its description, and its instructions. Description and
     instructions each open an editor with Save and Cancel. A failed save keeps the editor
     open with the text and says why. The instructions are edited without the copies of
     frontmatter some stored bodies begin with; those are written back as they were. */
  let { skill, first = false, rowIdPrefix, descriptionAriaLabel, bodyAriaLabel, bodyPlaceholder, onSave }: {
    skill: ManagedSkill;
    first?: boolean;
    /** `agents:<id>`; the description row is `<prefix>:description`. */
    rowIdPrefix: string;
    descriptionAriaLabel: string;
    bodyAriaLabel: string;
    bodyPlaceholder: string;
    onSave: (field: ManagedSkillField, raw: string) => Promise<void>;
  } = $props();

  type Editor = { draft: string | null; saving: boolean; error: unknown };
  const description = $state<Editor>({ draft: null, saving: false, error: null });
  const body = $state<Editor>({ draft: null, saving: false, error: null });
  let split = $derived(splitSkillFrontmatter(skill.markdown_body));

  function open(editor: Editor, text: string): void {
    editor.draft = text;
    editor.error = null;
  }

  function cancel(editor: Editor): void {
    if (editor.saving) return;
    editor.draft = null;
    editor.error = null;
  }

  async function save(editor: Editor, field: ManagedSkillField, shown: string, toStored: (text: string) => string): Promise<void> {
    if (editor.draft === null || editor.saving) return;
    if (editor.draft === shown) { cancel(editor); return; }
    editor.saving = true;
    editor.error = null;
    try {
      await onSave(field, toStored(editor.draft));
      editor.draft = null;
    } catch (error) {
      editor.error = error;
    } finally {
      editor.saving = false;
    }
  }

  function saveDescription(): Promise<void> {
    return save(description, "description", skill.description, (text) => text);
  }

  function saveBody(): Promise<void> {
    return save(body, "markdown_body", split.body, (text) => split.frontmatter + text);
  }

  function keys(event: KeyboardEvent, editor: Editor, commit: () => Promise<void>): void {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void commit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancel(editor);
    }
  }

  function focus(node: HTMLTextAreaElement): void {
    node.focus();
  }
</script>

{#snippet form(editor: Editor, commit: () => Promise<void>, label: string, mono: boolean, placeholder: string)}
  <div class="settings-skill-form" class:settings-skill-form--body={mono}>
    <textarea class="settings-skill-text" class:settings-skill-text--mono={mono} rows={mono ? undefined : 3}
      aria-label={label} {placeholder} disabled={editor.saving} value={editor.draft ?? ""}
      oninput={(event) => (editor.draft = event.currentTarget.value)}
      onkeydown={(event) => keys(event, editor, commit)} use:focus></textarea>
    {#if editor.error}<ErrorLine error={editor.error} />{/if}
    <div class="settings-skill-actions">
      <Button variant="primary" disabled={editor.saving} onclick={() => void commit()}>{editor.saving ? "Saving…" : "Save"}</Button>
      <Button variant="ghost" disabled={editor.saving} onclick={() => cancel(editor)}>Cancel</Button>
    </div>
  </div>
{/snippet}

<SettingsSection title="Skill" {first}>
  <SettingsRow label="Name">
    {#snippet control()}<span class="settings-skill-value" data-skill-name>{skill.name}</span>{/snippet}
  </SettingsRow>
  {#if description.draft === null}
    <SettingsRow label="Description" description={skill.description} rowId={`${rowIdPrefix}:description`}>
      {#snippet control()}<Button variant="ghost" onclick={() => open(description, skill.description)}>Edit</Button>{/snippet}
    </SettingsRow>
  {:else}
    <SettingsRow label="Description" rowId={`${rowIdPrefix}:description`}>
      {#snippet after()}{@render form(description, saveDescription, descriptionAriaLabel, false, "")}{/snippet}
    </SettingsRow>
  {/if}
</SettingsSection>

<SettingsSection title="Instructions" rows={false}>
  {#snippet action()}
    {#if body.draft === null}<Button variant="ghost" onclick={() => open(body, split.body)}>Edit</Button>{/if}
  {/snippet}
  {#if body.draft === null}
    <SettingsMarkdownWell markdown={skill.markdown_body} />
  {:else}
    {@render form(body, saveBody, bodyAriaLabel, true, bodyPlaceholder)}
  {/if}
</SettingsSection>

<style>
  .settings-skill-value { flex:none; color:var(--tone-secondary); font-size:var(--type-row); font-variant-numeric:tabular-nums; white-space:nowrap; }
  .settings-skill-form { display:grid; gap:var(--space-3); padding:0 0 var(--space-4); }
  .settings-skill-form--body { padding-top:var(--space-3); }
  .settings-skill-text { width:100%; min-height:32px; padding:var(--space-2); border:var(--border-hairline) solid var(--border-input); border-radius:var(--radius-md); background:var(--surface-input); color:var(--tone-primary); font-size:var(--type-row); line-height:1.55; resize:vertical; }
  .settings-skill-text:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--focus-offset); }
  .settings-skill-text--mono { min-height:320px; font:var(--type-code)/1.6 var(--font-mono); }
  .settings-skill-actions { display:flex; align-items:center; gap:var(--space-2); }
</style>
