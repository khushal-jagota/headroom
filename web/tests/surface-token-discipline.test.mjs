/**
 * Keep surface roles centralized and semantic. This is intentionally a source
 * contract: browser-css.test.mjs covers the corresponding computed behavior.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const tokensPath = join(repositoryRoot, "assets", "tokens.css");
const appPath = join(repositoryRoot, "assets", "app.css");

function svelteFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return svelteFiles(path);
    return extname(path) === ".svelte" ? [path] : [];
  });
}

const tokensSource = readFileSync(tokensPath, "utf8");
const auditedSources = [appPath, ...svelteFiles(join(webRoot, "src"))].map((path) => ({
  path,
  source: readFileSync(path, "utf8"),
}));

const expected = {
  "--surface-1": "#1b1917",
  "--surface-2": "#24211b",
  "--surface-recessed": "#141210",
  "--surface-ink": "#111318",
  "--interaction-hover":
    "linear-gradient( rgb(230 210 175 / 5%), rgb(230 210 175 / 5%) )",
  "--scrim": "rgb(10 9 8 / 55%)",
  "--shadow-float": "0 var(--space-2) var(--space-5) rgb(10 9 8 / 45%)",
};
const deprecated = [
  "--surface-base",
  "--surface-raised",
  "--surface-overlay",
  "--surface-sunken",
  "--surface-scrim",
  "--surface-default",
  "--surface-inset",
  "--accent-ink",
];

const definitions = [...tokensSource.matchAll(/(^|\n)\s*(--[a-z0-9-]+)\s*:\s*([^;]+);/g)];
const values = new Map(
  definitions.map((match) => [match[2], match[3].trim().replace(/\s+/g, " ")]),
);
for (const [name, value] of Object.entries(expected)) {
  assert.equal(
    definitions.filter((match) => match[2] === name).length,
    1,
    `${name} must have one canonical definition`,
  );
  assert.equal(values.get(name), value, `${name} must retain the approved baseline`);
}

for (const { path, source } of [{ path: tokensPath, source: tokensSource }, ...auditedSources]) {
  for (const name of deprecated) {
    assert.equal(source.includes(name), false, `${path} still uses deprecated ${name}`);
  }
}

const governedUse = /var\((--(?:surface-[a-z0-9-]+|interaction-hover|scrim|shadow-float))\b/g;
for (const { path, source } of auditedSources) {
  for (const match of source.matchAll(governedUse)) {
    assert.ok(values.has(match[1]), `${path} uses unresolved ${match[1]}`);
  }

  for (const match of source.matchAll(/([a-z-]+)\s*:\s*([^;{}]+)/g)) {
    const property = match[1];
    const value = match[2];
    assert.equal(
      property.startsWith("border") && /var\(--surface-/.test(value),
      false,
      `${path} uses a surface token as a border`,
    );
    assert.equal(
      /var\(--interaction-hover\)/.test(value) && property !== "background-image",
      false,
      `${path} must apply --interaction-hover as a relative background image`,
    );
    assert.equal(
      /var\(--scrim\)/.test(value) && !["background", "background-color"].includes(property),
      false,
      `${path} must reserve --scrim for backdrops`,
    );
    assert.equal(
      /var\(--shadow-float\)/.test(value) && property !== "box-shadow",
      false,
      `${path} must reserve --shadow-float for floating shadows`,
    );
    assert.equal(
      ["background", "background-color"].includes(property)
        && /^(?:#[0-9a-f]+|rgba?\(|hsla?\(|color-mix\()/i.test(value.trim()),
      false,
      `${path} bypasses the shared tokens with a derived or hardcoded background`,
    );
  }
}

console.log("surface token discipline: ok");
