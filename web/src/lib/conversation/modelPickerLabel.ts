import type { BackendModel } from "./wire";

/** Match Claude's versioned_display_name: dates are release stamps, [1m] is context. */
export function readableResolvedModel(identifier: string): string {
  const largeContext = identifier.endsWith("[1m]");
  const base = largeContext ? identifier.slice(0, -4) : identifier;
  const [provider, family, ...parts] = base.split("-");
  if (provider === "claude" && family) {
    const version = parts.filter((part) => !/^\d{8}$/.test(part));
    if (version.every((part) => /^\d+$/.test(part))) {
      const name = family[0].toUpperCase() + family.slice(1);
      return `${name}${version.length ? ` ${version.join(".")}` : ""}${largeContext ? " (1M)" : ""}`;
    }
  }
  if (/^gpt-\d+(?:\.\d+)*(?:-[a-z0-9]+)*$/.test(identifier)) {
    // Codex names its models "GPT-6.1 Sol": the version joins the family, words follow.
    const [version, ...words] = identifier.slice(4).split("-");
    return [`GPT-${version}`, ...words.map((part) => part[0].toUpperCase() + part.slice(1))].join(" ");
  }
  return /^(opus|sonnet|haiku)(\[1m\])?$/i.test(identifier)
    ? `${identifier.split("[")[0][0].toUpperCase()}${identifier.split("[")[0].slice(1)}${largeContext ? " (1M)" : ""}`
    : identifier;
}

/** Both the closed face and model options name the resolution, without a second alias. */
export function resolvedModelPickerLabel(
  models: readonly BackendModel[],
  value: string
): string {
  const selected = models.find((model) => model.model_id === value);
  // A value the catalogue does not list still reads as its model when its id is a
  // recognised form; anything else is shown as itself.
  if (!selected) return readableResolvedModel(value);
  const resolved = selected?.resolved_model_id;
  if (!resolved) return selected?.display_name ?? value;
  const alias = selected?.selected_variant;
  if (resolved === alias) return selected?.display_name ?? resolved;

  const concrete = models.find((model) => model.model_id === resolved);
  const label = concrete?.display_name;
  // Cached catalogue decoration can give a concrete entry its bare variant name.
  // That name is not evidence that the resolved version is unknown.
  if (label && label !== resolved && (!alias || label.toLowerCase() !== alias.toLowerCase())) {
    return label;
  }
  return readableResolvedModel(resolved);
}

const EFFORT_LABELS: Readonly<Record<string, string>> = {
  minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max"
};

/** A reasoning effort as a word a person reads: "medium" shows as "Medium". */
export function readableReasoningEffort(effort: string): string {
  if (effort === "") return "";
  return EFFORT_LABELS[effort] ?? effort[0].toUpperCase() + effort.slice(1);
}
