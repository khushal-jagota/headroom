<script lang="ts">
  /** One of the values the next message runs under, picked from a panel above the footer.
   *
   * The pill is the whole of the control at rest: the value in force, in as much room as
   * its own name takes and no more. Opening it is what puts a list on screen, so a footer
   * with two of these in it is two short words rather than two boxes sized for the longest
   * thing they could ever hold.
   *
   * A long list is searched and a short one is not, because a filter over four words is a
   * field to tab past rather than a way to find anything. Either way the keys are the
   * same: arrows move the highlight, Enter takes it, Escape leaves without taking
   * anything. Escape hands the keyboard back to the pill it came from; taking a value
   * hands it wherever the caller wants it, which in a composer is the message box, because
   * what a person does after picking a model is carry on writing.
   *
   * A panel can be given a rail, which is drawn down its left and is the caller's own — it
   * is where something that changes what the list is made of goes. This knows nothing about
   * what that is, only that the list belongs beside it.
   *
   * Nothing here decides anything. What the choices are, what is showing, and what a taken
   * value means are all the caller's, and this draws them.
   */
  import type { Snippet } from "svelte";

  type Choice = {
    /** The value a taken choice hands back — what actually goes on the wire. */
    value: string;
    /** The name a person reads this choice under. */
    name: string;
    /** One quieter line under the name, where the catalog has one. */
    detail: string | null;
  };

  let {
    label,
    choices,
    value,
    searchable = false,
    disabled = false,
    title = undefined,
    attributes = {},
    rail,
    onChoose
  }: {
    /** What this picker picks, in the words it is announced with: "Model". */
    label: string;
    /** The choices in the order they were offered in. */
    choices: readonly Choice[];
    /** The value in force, which is the pill's face. Empty is a picker with nothing to
     *  show yet: it keeps the affordance and gives up the rest. */
    value: string;
    /** Whether the list is long enough to be worth filtering. */
    searchable?: boolean;
    disabled?: boolean;
    title?: string | undefined;
    /** What this picker is, for whoever mounted it — which of them it is is their fact,
     *  not something this component could know about itself. */
    attributes?: Record<string, string | undefined>;
    /** Drawn down the left of the panel, where something that changes what is in the list
     *  goes. A picker given none is a panel of its list and nothing else. */
    rail?: Snippet;
    onChoose: (value: string) => void;
  } = $props();

  const rowIdStem = $props.id();

  let open = $state(false);
  let typed = $state("");
  let activeIndex = $state(0);
  let rootElement = $state<HTMLDivElement | null>(null);
  let triggerElement = $state<HTMLButtonElement | null>(null);
  let searchElement = $state<HTMLInputElement | null>(null);
  let listElement = $state<HTMLDivElement | null>(null);

  let face = $derived(choices.find((choice) => choice.value === value)?.name ?? value);
  let matching = $derived(choicesMatching(choices, typed));
  // Which row the highlight is really on. A list can shorten under it — the filter
  // narrows, or the catalog changes while the panel is open — and a highlight past the
  // end is a highlight on nothing, so it falls back to the top.
  let active = $derived(matching.length === 0 ? 0 : Math.min(activeIndex, matching.length - 1));

  // The highlight is scrolled to rather than left below the fold: an arrow key that moved
  // something nobody can see looks like a key that did nothing.
  $effect(() => {
    active;
    listElement
      ?.querySelector<HTMLElement>("[data-conversation-picker-active]")
      ?.scrollIntoView({ block: "nearest" });
  });

  // Opening puts the keyboard where the typing goes — the filter when there is one, the
  // list itself when there is not, so the arrows work either way.
  $effect(() => {
    if (!open) return;
    (searchElement ?? listElement)?.focus();
  });

  /** What was typed belongs to the list it was typed against.
   *
   * Hand the picker a different list — a rail changed what is on offer — and the words in
   * the filter are not a search any more, they are the last list's words hiding this one.
   * Compared rather than watched, so opening a panel that was closed with a filter in it
   * clears that filter without also moving the highlight the opening just placed.
   */
  let whatIsOnOffer = $derived(choices.map((choice) => choice.value).join("\n"));
  let listTypedAgainst = "";
  $effect(() => {
    if (listTypedAgainst === whatIsOnOffer) return;
    listTypedAgainst = whatIsOnOffer;
    typed = "";
  });

  // A picker that has just been taken away from the person cannot be left standing open
  // over a composer they can no longer use.
  $effect(() => {
    if (disabled) open = false;
  });

  // Anywhere else is not this panel. Pressing rather than clicking, so the panel is gone
  // before whatever was pressed acts on the press.
  $effect(() => {
    if (!open) return;
    function pressedSomewhereElse(event: PointerEvent): void {
      const pressed = event.target;
      if (rootElement !== null && pressed instanceof Node && !rootElement.contains(pressed)) {
        open = false;
      }
    }
    document.addEventListener("pointerdown", pressedSomewhereElse, true);
    return () => document.removeEventListener("pointerdown", pressedSomewhereElse, true);
  });

  /** The choices a typed word reaches, in the order they were offered in.
   *
   * Everything a row shows is searched — the name, the quieter line under it, and the
   * value itself — because a person hunting for a model types whichever of those they
   * happen to remember.
   */
  function choicesMatching(all: readonly Choice[], wanted: string): Choice[] {
    const looking = wanted.trim().toLowerCase();
    if (looking === "") return [...all];
    return all.filter((choice) =>
      [choice.name, choice.detail ?? "", choice.value].some((written) =>
        written.toLowerCase().includes(looking)
      )
    );
  }

  function openThePanel(): void {
    typed = "";
    open = true;
    const showing = choices.findIndex((choice) => choice.value === value);
    activeIndex = showing === -1 ? 0 : showing;
  }

  /** Close without taking anything, and hand the keyboard back to the pill it came from. */
  function closeAndGiveThePillTheKeyboard(): void {
    open = false;
    triggerElement?.focus();
  }

  function take(choice: Choice): void {
    open = false;
    onChoose(choice.value);
  }

  /** Leaving the panel, from anywhere in it — the filter, the list, or a rail. */
  function onPanelKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      closeAndGiveThePillTheKeyboard();
      return;
    }
    if (event.key === "Tab") open = false;
  }

  /** Working the list, which is only ever done from the filter or the list itself. A rail
   *  is somewhere else in the panel with controls of its own, and Enter there is theirs. */
  function onListKeydown(event: KeyboardEvent): void {
    if (matching.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : matching.length - 1;
      activeIndex = (active + step) % matching.length;
      return;
    }
    const highlighted = matching[active];
    if (event.key === "Enter" && highlighted !== undefined) {
      event.preventDefault();
      take(highlighted);
    }
  }
</script>

<div class="c2-pick" bind:this={rootElement} {...attributes}>
  <button
    type="button"
    class="c2-pick-pill"
    class:is-open={open}
    class:is-bare={face === ""}
    bind:this={triggerElement}
    data-conversation-picker-trigger
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-label={face === "" ? label : `${label}: ${face}`}
    {title}
    {disabled}
    onclick={() => (open ? closeAndGiveThePillTheKeyboard() : openThePanel())}
  >
    {#if face !== ""}
      <span class="c2-pick-face">{face}</span>
    {/if}
    <span class="c2-pick-chevron" class:is-open={open} aria-hidden="true">›</span>
  </button>

  {#if open}
    <!-- Escape is caught for the whole panel, so it leaves from wherever the keyboard is
         in it — including a rail, whose own controls this knows nothing about. -->
    <div
      class="chat-menu c2-pick-panel"
      class:has-rail={rail !== undefined}
      data-conversation-picker-panel
      role="presentation"
      onkeydown={onPanelKeydown}
    >
      {#if rail}
        <div class="c2-pick-rail">{@render rail()}</div>
      {/if}
      <div class="c2-pick-body">
        {#if searchable}
          <!-- The filter is where the keyboard lands on a searchable picker, so it carries
               the keys the list is worked with as well as the ones it is typed with. -->
          <input
            class="c2-pick-search"
            type="text"
            bind:this={searchElement}
            bind:value={typed}
            data-conversation-picker-search
            placeholder="Search"
            role="combobox"
            aria-label={`Search ${label.toLowerCase()}`}
            aria-autocomplete="list"
            aria-expanded="true"
            aria-controls={rowIdStem}
            aria-activedescendant={matching.length === 0 ? undefined : `${rowIdStem}-${active}`}
            onkeydown={onListKeydown}
            oninput={() => (activeIndex = 0)}
          />
        {/if}
        <div
          class="c2-pick-list"
          id={rowIdStem}
          bind:this={listElement}
          role="listbox"
          aria-label={label}
          aria-activedescendant={searchable || matching.length === 0
            ? undefined
            : `${rowIdStem}-${active}`}
          tabindex="-1"
          onkeydown={onListKeydown}
        >
          {#each matching as choice, index (choice.value)}
            <!-- Pressing a row leaves the keyboard where it is: the press that chooses must
                 not take the panel out from under itself on the way. -->
            <button
              type="button"
              class="chat-menu-item c2-pick-row"
              class:on={index === active}
              id={`${rowIdStem}-${index}`}
              role="option"
              aria-selected={choice.value === value}
              data-conversation-picker-choice={choice.value}
              data-conversation-picker-active={index === active ? "true" : undefined}
              data-conversation-picker-chosen={choice.value === value ? "true" : undefined}
              onmouseenter={() => (activeIndex = index)}
              onmousedown={(event) => event.preventDefault()}
              onclick={() => take(choice)}
            >
              <span class="c2-pick-lines">
                <span class="c2-pick-name">{choice.name}</span>
                {#if choice.detail}
                  <span class="c2-pick-detail">{choice.detail}</span>
                {/if}
              </span>
              <span class="c2-pick-mark" aria-hidden="true"
                >{choice.value === value ? "✓" : ""}</span
              >
            </button>
          {/each}
          {#if matching.length === 0}
            <div class="chat-menu-hd">No {label.toLowerCase()} matches that.</div>
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .c2-pick { position: relative; display: inline-flex; min-width: 0; }
  /* Its own name's worth of room and nothing more, down to the chevron alone when there
     is no value to show. */
  .c2-pick-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    min-width: 0;
    max-width: 100%;
    background: transparent;
    border: var(--border-hairline) solid transparent;
    border-radius: var(--radius-pill);
    color: var(--text-muted);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    line-height: 1.2;
    padding: var(--space-1) var(--space-2);
  }
  .c2-pick-pill:hover { border-color: var(--border-color); color: var(--text-strong); }
  .c2-pick-pill.is-open {
    background: var(--surface-overlay);
    border-color: var(--border-color);
    color: var(--text-strong);
  }
  .c2-pick-pill:disabled { cursor: default; opacity: 0.5; }
  .c2-pick-pill.is-bare { padding-inline: var(--space-1); }
  .c2-pick-face { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .c2-pick-chevron {
    flex: none;
    display: inline-block;
    transform: rotate(90deg);
    transition: transform var(--motion-fast) var(--motion-ease);
  }
  .c2-pick-chevron.is-open { transform: rotate(-90deg); }
  /* The popover the app already has, anchored to this pill rather than to the whole box,
     and only as wide as its own rows need. */
  .c2-pick-panel {
    left: 0;
    right: auto;
    display: grid;
    gap: var(--space-1);
    width: max-content;
    min-width: 100%;
    /* As wide as its rows need, and never wider than the window it floats in — a phone's
       worth of screen is narrower than the list would like to be. */
    max-width: min(calc(var(--type-xs) * 26), calc(100vw - var(--space-7) * 2));
    max-height: none;
    overflow: hidden;
  }
  /* With a rail the panel is two columns: the rail takes what it needs and the list has
     the rest. */
  .c2-pick-panel.has-rail {
    grid-template-columns: auto minmax(0, 1fr);
    max-width: min(calc(var(--type-xs) * 38), calc(100vw - var(--space-7) * 2));
  }
  .c2-pick-rail {
    border-inline-end: var(--border-hairline) solid var(--border-color);
    padding-inline-end: var(--space-1);
  }
  .c2-pick-body { display: grid; gap: var(--space-1); min-width: 0; }
  /* The list scrolls, not the panel: a filter that scrolled away from the person typing
     into it would be a filter they lose as soon as they use it. */
  .c2-pick-list { max-height: calc(var(--type-xs) * 22); overflow-y: auto; }
  .c2-pick-search {
    width: 100%;
    background: transparent;
    border: 0;
    border-bottom: var(--border-hairline) solid var(--border-color);
    border-radius: 0;
    color: var(--text-strong);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    outline: none;
    padding: var(--space-1) var(--space-2);
  }
  .c2-pick-search::placeholder { color: var(--text-faintest); }
  .c2-pick-row { align-items: center; justify-content: space-between; }
  /* Where the keyboard is, said in the surface rather than in a colour — and said loudly
     enough to follow an arrow key by, which the raised-on-raised menu highlight is not. */
  .c2-pick-row.on { background: var(--surface-sunken); }
  .c2-pick-row.on .c2-pick-name { color: var(--text-strong); }
  .c2-pick-lines { display: grid; min-width: 0; }
  .c2-pick-name {
    color: var(--text-default);
    font-size: var(--type-sm);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* What this choice really is, where the catalog says — an alias and the version it
     reaches. It reads as the quieter half of one row, not as a line of its own. */
  .c2-pick-detail {
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .c2-pick-mark {
    flex: none;
    width: var(--space-4);
    color: var(--text-strong);
    font-size: var(--type-xs);
    text-align: right;
  }
  .c2-pick-row[data-conversation-picker-chosen] .c2-pick-name { color: var(--text-strong); }
  @media (prefers-reduced-motion: reduce) {
    .c2-pick-chevron { transition: none; }
  }
</style>
