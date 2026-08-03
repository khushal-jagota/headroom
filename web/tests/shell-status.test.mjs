import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const shellStatus = await readFile(
  new URL("../src/components/ShellStatus.svelte", import.meta.url),
  "utf8",
);
const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");

// The shell has one semantic status source. The deployment status is an
// accessible trigger for on-demand detail, while worker presence remains a
// separate, quiet signal in the same cluster.
assert.match(shellStatus, /role="status"/);
assert.match(shellStatus, /aria-live="polite"/);
assert.match(shellStatus, /data-shell-status/);
assert.match(shellStatus, /data-connection-status/);
assert.match(
  shellStatus,
  /connected: "Connected".*reconnecting: "Reconnecting".*preparing: "Preparing".*restarting: "Restarting".*back_up: "Back up".*problem: "Problem"/s,
);
assert.match(shellStatus, /queries\.deploymentStatus\(\)/);
assert.match(shellStatus, /queries\.vpsStatusSummary\(\)/);
assert.match(shellStatus, /enabled: isOpen/);
assert.match(shellStatus, /resolveDeploymentStatus/);
assert.match(shellStatus, /deploymentStatusExpiryDelay/);
assert.match(shellStatus, /aria-expanded=\{isOpen\}/);
assert.match(shellStatus, /aria-controls="shell-vps-status-popover"/);
assert.match(shellStatus, /data-vps-status-content/);
assert.match(shellStatus, /Deployed commit/);
assert.match(shellStatus, /Latest verified backup/);
assert.match(shellStatus, /VPS status is unavailable/);
assert.match(shellStatus, /\{runningWorkerCount\} working/);
assert.doesNotMatch(shellStatus, /Environment|Worktree|Logs|Cleanup|overall_state|collected_at/);

assert.match(
  app,
  /<ShellStatus[\s\S]*connectionState=\{\$connectionStatus\}[\s\S]*runningWorkerCount=/,
);
assert.equal((app.match(/<ShellStatus/g) || []).length, 1);
assert.doesNotMatch(app, /VpsStatusPopover|#\/status|name === "status"/);

console.log("shell-status.test.mjs: all assertions passed");
