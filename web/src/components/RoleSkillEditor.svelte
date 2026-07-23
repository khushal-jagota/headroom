<script lang="ts">
  import InlineEdit from "./InlineEdit.svelte";
  import type { ManagedSkill } from "../lib/types";

  let {
    heading,
    skill,
    descriptionAriaLabel,
    bodyAriaLabel,
    bodyPlaceholder,
    onSave
  }: {
    heading: string;
    skill: ManagedSkill;
    descriptionAriaLabel: string;
    bodyAriaLabel: string;
    bodyPlaceholder: string;
    onSave: (field: "description" | "markdown_body", raw: string) => Promise<void>;
  } = $props();
</script>

<section class="worker-skill" data-role-skill>
  <header class="worker-skill-head"><h2>{heading}</h2></header>
  <div class="worker-skill-content" data-skill-content>
    <dl class="worker-skill-meta">
      <div>
        <dt>Name</dt>
        <dd data-skill-name>{skill.name}</dd>
      </div>
      <div>
        <dt>Description</dt>
        <dd data-skill-description>
          <InlineEdit
            value={skill.description}
            multiline
            ariaLabel={descriptionAriaLabel}
            className="worker-skill-inline"
            onSave={(raw) => onSave("description", raw)}
          />
        </dd>
      </div>
    </dl>
    <div class="worker-skill-field">
      <span>Markdown body</span>
      <div class="worker-skill-body" data-skill-body>
        <InlineEdit
          value={skill.markdown_body}
          markdown
          multiline
          placeholder={bodyPlaceholder}
          ariaLabel={bodyAriaLabel}
          className="worker-skill-body-editable"
          onSave={(raw) => onSave("markdown_body", raw)}
        />
      </div>
    </div>
  </div>
</section>
