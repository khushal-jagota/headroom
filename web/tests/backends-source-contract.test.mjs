import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const route = await readFile(new URL("../src/routes/BackendsRoute.svelte", import.meta.url), "utf8");
const picker = await readFile(
  new URL("../src/components/conversation/UnifiedModelPicker.svelte", import.meta.url),
  "utf8"
);
const composerControls = await readFile(
  new URL("../src/components/conversation/composer/ComposerRunControls.svelte", import.meta.url),
  "utf8"
);
const ticketPicker = await readFile(
  new URL("../src/components/WorkerConfigurationSetup.svelte", import.meta.url),
  "utf8"
);
const managedPicker = await readFile(
  new URL("../src/components/ManagedLaunchDefaults.svelte", import.meta.url),
  "utf8"
);

assert.match(route, /checked=\{model\.enabled\}/);
assert.match(route, /onchange=\{\(event\) => void setModelEnabled/);
assert.match(route, /isSignedOut\(snapshot\)/);
assert.match(route, /snapshot\.identity\.login_command/);
assert.match(route, /<UsageRings/);
assert.doesNotMatch(route, /BackendCard|refreshBackendUsage/);

assert.match(picker, /showUsage && showsBackendUsage/);
assert.match(picker, /showUsage && showing === "models" && hasModelUsage/);
assert.match(composerControls, /showUsage/);
assert.doesNotMatch(ticketPicker, /showUsage/);
assert.doesNotMatch(managedPicker, /showUsage/);
