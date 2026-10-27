<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import ResourceState from '../components/ResourceState.svelte';
  import { resourceStateForQueries } from '../lib/resourceStateForQueries';
  import { queries } from '../lib/queryCatalogue';
  import SourceCard from '../lib/measureSources/SourceCard.svelte';
  import SourceControls from '../lib/measureSources/SourceControls.svelte';

  let projectId = $state('');
  let offset = $state(0);
  let selectedId = $state('');
  const projects = createQuery(() => queries.projects());
  const sources = createQuery(() => queries.measureSources(projectId, offset));
  function changeProject() { offset = 0; selectedId = ''; }
</script>
<section class="measure-sources" data-screen="measure-sources">
  <header><div><h1>Measure sources</h1><p>Automatic observations for project measures. Configure scripts and outputs through the API or CLI.</p></div>
    <label>Project <select bind:value={projectId} onchange={changeProject}><option value="">All projects</option>
      {#each projects.data?.projects ?? [] as project (project.id)}<option value={project.id}>{project.name}</option>{/each}
    </select></label></header>
  <ResourceState shape="block" {...resourceStateForQueries(sources)} loadingText="Loading measure sources…">
    {#if !sources.data?.sources.length}<p>No measure sources configured.</p>{/if}
    {#each sources.data?.sources ?? [] as status (status.source.id)}
      <div class="source-group"><SourceCard {status} projectName={projects.data?.projects.find(item => item.id === status.source.scope.project_id)?.name ?? 'Project'} selected={selectedId === status.source.id}
        onselect={() => selectedId = selectedId === status.source.id ? '' : status.source.id} />
        {#if selectedId === status.source.id}<SourceControls {status} />{/if}
      </div>
    {/each}
    <nav aria-label="Source pages">{#if offset > 0}<button onclick={() => { offset = Math.max(0, offset - 20); selectedId = ''; }}>Previous sources</button>{/if}
      {#if sources.data?.page.next_offset != null}<button onclick={() => { offset = sources.data?.page.next_offset ?? 0; selectedId = ''; }}>More sources</button>{/if}
    </nav>
  </ResourceState>
</section>
<style>
  .measure-sources :global(button), .measure-sources :global(select), .measure-sources :global(input:not([type=checkbox])), .measure-sources :global(textarea) {
    font:inherit; color:var(--text-default); background:var(--surface-input); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); padding:var(--space-2) var(--space-3);
  }
  .measure-sources :global(button) { background:var(--surface-2); cursor:pointer; }
  .measure-sources :global(button:hover:not(:disabled)) { border-color:var(--accent-bright); color:var(--text-strong); }
  .measure-sources :global(button:disabled) { opacity:0.5; cursor:default; }
  .measure-sources :global(:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  .measure-sources { display:grid; gap:var(--space-4); padding:var(--space-4) 0; }
  header { display:flex; justify-content:space-between; gap:var(--space-4); align-items:start; flex-wrap:wrap; }
  h1 { margin:0; font-size:var(--type-lg); } p { color:var(--text-muted); max-width:48rem; }
  label { display:grid; gap:var(--space-1); } .source-group { margin-bottom:var(--space-3); } nav { display:flex; gap:var(--space-3); }
</style>
