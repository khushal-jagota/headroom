<script lang="ts">
  import example from "../../../src/planner/page_kit/examples/proposal-page.json";
  import PageBlocks from "../components/pageKit/PageBlocks.svelte";
  import PageDocument from "../components/pageKit/PageDocument.svelte";
  import PageTable from "../components/pageKit/PageTable.svelte";
  import { definePage, type Block, type ChecklistBlock, type MetricsBlock, type PageDocument as PageDocumentValue, type RowsBlock, type SectionBlock, type TableBlock } from "../lib/pageKit";

  const proposalPage = example as PageDocumentValue;

  function walk(blocks: Block[]): Block[] {
    const found: Block[] = [];
    for (const block of blocks) {
      found.push(block);
      if (block.type === "section") {
        if (block.blocks) found.push(...walk(block.blocks));
        for (const view of block.views ?? []) found.push(...walk(view.blocks));
      } else if (block.type === "grid") {
        found.push(...walk(block.blocks));
      } else if (block.type === "segmented") {
        found.push(...walk(block.views.flatMap((view) => view.blocks)));
      }
    }
    return found;
  }

  function first<T extends Block["type"]>(type: T): Extract<Block, { type: T }> {
    const block = walk(proposalPage.blocks).find((item) => item.type === type);
    if (!block || block.type !== type) throw new Error(`example is missing ${type}`);
    return block as Extract<Block, { type: T }>;
  }

  const metrics: MetricsBlock = first("metrics");
  const table: TableBlock = {
    type: "table",
    id: "where-it-shows",
    caption: "Where it shows",
    columns: ["Surface", "What the reader gets", "Checks"],
    rows: [
      ["Ticket proposal", "The page, with Approve", "12"],
      ["Review", "The same page", "9"],
      ["Settled field", "The approved page", "12"],
      ["Goal page", "The same document later", "4"],
      ["Prose proposal", "The note, unchanged", "0"],
      ["Worker transcript", "Left closed", "3"],
      ["Later goal", "The same kit", "7"]
    ]
  };
  const checklist: ChecklistBlock = first("checklist");
  const rows: RowsBlock = first("rows");
  const readiness: SectionBlock = proposalPage.blocks.find(
    (block): block is SectionBlock => block.type === "section" && block.title === "Readiness"
  ) ?? first("section");
  const headerPage = definePage({
    kind: "headroom.page",
    version: 1,
    title: proposalPage.title,
    summary: proposalPage.summary,
    blocks: []
  });

  let column = $state(672);

  const links = [
    ["document", "Document"],
    ["header", "Header"],
    ["metrics", "Metrics"],
    ["table", "Table"],
    ["checklist", "Checklist"],
    ["surfaces", "Surfaces"],
    ["views", "Views"]
  ] as const;
</script>

<div class="playground">
  <header class="bar">
    <p class="name">Page kit</p>
    <nav aria-label="Sections">
      {#each links as [id, label] (id)}
        <a href={`#${id}`}>{label}</a>
      {/each}
    </nav>
    <label class="width">
      Column
      <input type="range" min="320" max="720" step="8" bind:value={column} />
      <span>{column}</span>
    </label>
  </header>

  <main>
    <section id="document">
      <h2>Document</h2>
      <p class="note">The worked example, in the same card the Ticket uses.</p>
      <div class="column" style:width="{column}px">
        <div class="stage">
          <p class="gate">Brief — for User to approve</p>
          <div class="mount"><PageDocument document={proposalPage} /></div>
        </div>
      </div>
    </section>

    <section id="header">
      <h2>Header</h2>
      <p class="note">The gate line, then the title, then the summary.</p>
      <div class="column" style:width="{column}px">
        <div class="stage">
          <p class="gate">Brief — for User to approve</p>
          <div class="mount"><PageDocument document={headerPage} /></div>
        </div>
      </div>
    </section>

    <section id="metrics">
      <h2>Metrics</h2>
      <p class="note">Sentence case, a large figure, and one context line. No strip.</p>
      <div class="column" style:width="{column}px">
        <div class="stage"><PageBlocks blocks={[metrics]} /></div>
      </div>
    </section>

    <section id="table">
      <h2>Table</h2>
      <p class="note">Sort a column, or select rows. Shift-click selects a range. Nothing here is stored.</p>
      <div class="column" style:width="{column}px">
        <div class="stage"><PageTable block={table} selectable /></div>
      </div>
    </section>

    <section id="checklist">
      <h2>Checklist</h2>
      <p class="note">Open a step, tick it, and let it fold into the completed group. Uncheck it from there. Nothing here is stored.</p>
      <div class="column" style:width="{column}px">
        <div class="stage"><PageBlocks blocks={[checklist]} /></div>
      </div>
    </section>

    <section id="surfaces">
      <h2>Surfaces</h2>
      <p class="note">Where the page shows. The status sits on the right.</p>
      <div class="column" style:width="{column}px">
        <div class="stage">
          <PageBlocks blocks={[{ type: "section", id: "surfaces-demo", title: "Surfaces", blocks: [rows] }]} />
        </div>
      </div>
    </section>

    <section id="views">
      <h2>Views</h2>
      <p class="note">Risks is the open choice. Evidence is a short table. The choice stays on this page.</p>
      <div class="column" style:width="{column}px">
        <div class="stage"><PageBlocks blocks={[readiness]} /></div>
      </div>
    </section>
  </main>
</div>

<style>
  .playground {
    min-height: 100vh;
    background: var(--surface-recessed);
    color: var(--text-default);
    font-family: var(--font-ui);
  }
  .bar {
    position: sticky;
    top: 0;
    z-index: 2;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-4) var(--space-5);
    padding: var(--space-4) var(--space-5);
    background: var(--surface-1);
    border-bottom: var(--border-hairline) solid var(--border-color);
  }
  .name {
    margin: 0;
    color: var(--text-strong);
    font-size: var(--type-md);
    font-weight: 500;
  }
  nav { display: flex; flex-wrap: wrap; gap: var(--space-4); }
  nav a {
    color: var(--text-muted);
    font-size: var(--type-sm);
    font-weight: 500;
    text-decoration: none;
  }
  nav a:hover { color: var(--text-strong); }
  nav a:focus-visible { outline: var(--border-strong) solid var(--accent-bright); outline-offset: 3px; }
  .width {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-left: auto;
    color: var(--text-faint);
    font-size: var(--type-sm);
    font-weight: 400;
    font-variant-numeric: tabular-nums;
  }
  .width input { width: 8rem; accent-color: var(--text-muted); }
  main {
    display: grid;
    gap: var(--space-7);
    padding: var(--space-6) var(--space-5) var(--space-7);
  }
  section { scroll-margin-top: 5rem; }
  h2 {
    margin: 0;
    color: var(--text-strong);
    font-size: var(--type-lg);
    font-weight: 500;
    letter-spacing: var(--tracking-title);
  }
  .note {
    margin: var(--space-2) 0 var(--space-4);
    max-width: 40rem;
    color: var(--text-faint);
    font-size: var(--type-sm);
    line-height: 1.5;
  }
  .column { max-width: 100%; }
  .stage {
    container-name: page;
    container-type: inline-size;
    padding: var(--space-5);
    background: var(--surface-2);
    border-radius: var(--radius-lg);
  }
  .gate {
    margin: 0;
    color: var(--text-muted);
    font-size: var(--type-sm);
    font-weight: 500;
    line-height: 1.4;
  }
  .mount { margin-top: var(--space-4); }
</style>
