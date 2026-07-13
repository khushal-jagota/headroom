import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

// Transpile ui.ts + lifecycle.ts into one temp dir and import lifecycle.mjs.
// verbatimModuleSyntax elides the type-only ./types imports; the surviving value
// import in lifecycle.ts is `from "./ui"`. transpileModule PRESERVES that
// extensionless specifier, and Node ESM will NOT resolve "./ui" to "ui.mjs" — so
// the harness rewrites `from "./ui"` -> `from "./ui.mjs"` before writing the file
// (Codex F5), or the import throws ERR_MODULE_NOT_FOUND before any assertion.
const compilerOptions = {
  module: ts.ModuleKind.ES2022,
  target: ts.ScriptTarget.ES2022,
  verbatimModuleSyntax: true
};

async function transpile(name) {
  const source = await readFile(new URL(`../src/lib/${name}.ts`, import.meta.url), "utf8");
  return ts.transpileModule(source, { compilerOptions }).outputText;
}

const dir = await mkdtemp(join(tmpdir(), "planner-lifecycle-"));
const uiOut = await transpile("ui");
const lifecycleOut = (await transpile("lifecycle")).replace(/from\s+["']\.\/ui["']/g, 'from "./ui.mjs"');
await writeFile(join(dir, "ui.mjs"), uiOut, "utf8");
const lifecyclePath = join(dir, "lifecycle.mjs");
await writeFile(lifecyclePath, lifecycleOut, "utf8");
const {
  buildLifecycle,
  lifecycleFor,
  gatingFieldFor,
  advanceTargetFor,
  ceilingOptionsFor,
  fieldIsPassedFor,
  ticketStageVisualStateFor,
  fieldStageVisualStateFor,
  recapVisibleFor
} = await import(lifecyclePath);
await rm(dir, { recursive: true, force: true });

// lifecycleFor is imported above from the REAL lifecycle.ts (not a copy) so Part B
// exercises the production selector + memoization (Codex F6).

// --- the coding manifest literal, exactly as GET /api/ticket-types serves it -----
const codingManifest = {
  type_id: "coding",
  label: "Coding",
  stages: [
    { id: "needs_kickoff", label: "Kickoff", gating_field: "kickoff", is_terminal: false },
    { id: "needs_success", label: "Success", gating_field: "success", is_terminal: false },
    { id: "needs_approach", label: "Approach", gating_field: "approach", is_terminal: false },
    { id: "needs_plan", label: "Plan", gating_field: "plan", is_terminal: false },
    {
      id: "needs_implementation",
      label: "Implementation",
      gating_field: "implementation",
      is_terminal: false
    },
    { id: "needs_closeout", label: "Closeout", gating_field: "closeout", is_terminal: false },
    { id: "done", label: "Done", gating_field: null, is_terminal: true }
  ],
  dropped: { id: "dropped", label: "Dropped", gating_field: null, is_terminal: true },
  advance: {
    needs_kickoff: "needs_success",
    needs_success: "needs_approach",
    needs_approach: "needs_plan",
    needs_plan: "needs_implementation",
    needs_implementation: "needs_closeout",
    needs_closeout: "done"
  },
  fields: [
    { id: "kickoff", label: "Kickoff" },
    { id: "success", label: "Success" },
    { id: "approach", label: "Approach" },
    { id: "plan", label: "Plan" },
    { id: "implementation", label: "Implementation" },
    { id: "closeout", label: "Closeout" }
  ],
  ceiling_range: [
    "needs_success",
    "needs_approach",
    "needs_plan",
    "needs_implementation",
    "needs_closeout",
    "done"
  ],
  default_ceiling: "needs_success",
  worker_profile_id: "panels-worker"
};

// --- Part A: coding byte-identity vs a HARDCODED copy of today's ui.ts maps ------
// These are the retired constants, hardcoded here (NOT derived from the manifest) —
// the assertion is that the manifest-built lifecycle reproduces them exactly.
const OLD_FIELD_NAMES = ["kickoff", "success", "approach", "plan", "implementation", "closeout"];
const OLD_STATE_ORDER = [
  "needs_kickoff",
  "needs_success",
  "needs_approach",
  "needs_plan",
  "needs_implementation",
  "needs_closeout",
  "done"
];
const OLD_GATING_FIELD = {
  needs_kickoff: "kickoff",
  needs_success: "success",
  needs_approach: "approach",
  needs_plan: "plan",
  needs_implementation: "implementation",
  needs_closeout: "closeout"
};
const OLD_ADVANCE = {
  needs_kickoff: "needs_success",
  needs_success: "needs_approach",
  needs_approach: "needs_plan",
  needs_plan: "needs_implementation",
  needs_implementation: "needs_closeout",
  needs_closeout: "done"
};

const coding = buildLifecycle(codingManifest);
assert.deepEqual(coding.fieldIds, OLD_FIELD_NAMES, "coding fieldIds == old FIELD_NAMES");
assert.deepEqual(coding.stateOrder, OLD_STATE_ORDER, "coding stateOrder == old STATE_ORDER");
assert.deepEqual(coding.gatingField, OLD_GATING_FIELD, "coding gatingField == old GATING_FIELD");
assert.deepEqual(coding.advance, OLD_ADVANCE, "coding advance == old ADVANCE");
assert.equal(coding.typeLabel, "Coding");

// The scope leash options keep the LOWERCASE stateLabel(id) labels ("needs success"),
// NOT the manifest's capitalized stage.label — the mockup wording must not change.
assert.deepEqual(ceilingOptionsFor(coding, "needs_success"), [
  { value: "needs_success", label: "needs success" },
  { value: "needs_approach", label: "needs approach" },
  { value: "needs_plan", label: "needs plan" },
  { value: "needs_implementation", label: "needs implementation" },
  { value: "needs_closeout", label: "needs closeout" },
  { value: "done", label: "done" }
]);
// A mid-range floor slices from that state.
assert.deepEqual(ceilingOptionsFor(coding, "needs_plan"), [
  { value: "needs_plan", label: "needs plan" },
  { value: "needs_implementation", label: "needs implementation" },
  { value: "needs_closeout", label: "needs closeout" },
  { value: "done", label: "done" }
]);

// gatingField / advanceTarget / fieldIsPassed classic cases.
assert.equal(gatingFieldFor(coding, "needs_success"), "success");
assert.equal(gatingFieldFor(coding, "done"), null);
assert.equal(advanceTargetFor(coding, "needs_plan", "done"), "needs_implementation");
assert.equal(advanceTargetFor(coding, "needs_closeout", "done"), "done");
assert.equal(advanceTargetFor(coding, "done", "done"), null);
assert.equal(fieldIsPassedFor(coding, "success", "needs_success"), false);
assert.equal(fieldIsPassedFor(coding, "success", "needs_approach"), true);
assert.equal(fieldIsPassedFor(coding, "closeout", "needs_success"), false);

// ticketStageVisualStateFor classic cases (running / errored / awaiting / waiting /
// passed / done / upcoming).
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "needs_success",
    ticketStatus: "agent_running_step",
    fieldName: "success"
  }),
  "current-running"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "needs_success",
    ticketStatus: "errored",
    fieldName: "success"
  }),
  "errored"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "needs_success",
    ticketStatus: "awaiting_approval",
    fieldName: "success"
  }),
  "current-awaiting-approval"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "needs_success",
    ticketStatus: "empty",
    fieldName: "success",
    fieldHasProposal: true
  }),
  "current-awaiting-approval"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "needs_success",
    ticketStatus: "empty",
    fieldName: "success"
  }),
  "current-waiting"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "needs_approach",
    ticketStatus: "empty",
    fieldName: "success"
  }),
  "completed"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "done",
    ticketStatus: "empty",
    fieldName: "closeout"
  }),
  "completed"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketState: "needs_success",
    ticketStatus: "empty",
    fieldName: "closeout"
  }),
  "upcoming"
);

// fieldStageVisualStateFor reads the slot proposal from the detail.
assert.equal(
  fieldStageVisualStateFor(
    coding,
    {
      state: "needs_success",
      ticket_status: "empty",
      fields: { success: { proposal: { body: "x", proposed_by: "worker" } } }
    },
    "success"
  ),
  "current-awaiting-approval"
);

// recapVisibleFor: hidden through needs_success, visible from needs_approach on.
assert.equal(recapVisibleFor(coding, "needs_kickoff"), false);
assert.equal(recapVisibleFor(coding, "needs_success"), false);
assert.equal(recapVisibleFor(coding, "needs_approach"), true);
assert.equal(recapVisibleFor(coding, "done"), true);

// --- Part A: pre-load null defaults (Codex F1) ----------------------------------
assert.equal(gatingFieldFor(null, "needs_success"), null);
assert.equal(advanceTargetFor(null, "needs_plan", "done"), null);
assert.deepEqual(ceilingOptionsFor(null, "needs_success"), []);
assert.equal(fieldIsPassedFor(null, "success", "needs_approach"), false);
assert.equal(
  ticketStageVisualStateFor(null, {
    ticketState: "needs_success",
    ticketStatus: "agent_running_step",
    fieldName: "success"
  }),
  "upcoming"
);
assert.equal(recapVisibleFor(null, "needs_approach"), false);

// --- Part B: a synthetic SECOND type proves the render logic is variable ----------
// research: needs_brief -> needs_findings -> needs_writeup -> done; fields brief/
// findings/writeup. Routed through the real lookup+memoize (lifecycleFor), NOT
// buildLifecycle(research) directly (Codex F6).
const researchManifest = {
  type_id: "research",
  label: "Research",
  stages: [
    { id: "needs_brief", label: "Brief", gating_field: "brief", is_terminal: false },
    { id: "needs_findings", label: "Findings", gating_field: "findings", is_terminal: false },
    { id: "needs_writeup", label: "Writeup", gating_field: "writeup", is_terminal: false },
    { id: "done", label: "Done", gating_field: null, is_terminal: true }
  ],
  dropped: { id: "dropped", label: "Dropped", gating_field: null, is_terminal: true },
  advance: {
    needs_brief: "needs_findings",
    needs_findings: "needs_writeup",
    needs_writeup: "done"
  },
  fields: [
    { id: "brief", label: "Brief" },
    { id: "findings", label: "Findings" },
    { id: "writeup", label: "Writeup" }
  ],
  ceiling_range: ["needs_findings", "needs_writeup", "done"],
  default_ceiling: "needs_findings",
  worker_profile_id: "research-worker"
};

const response = { types: [codingManifest, researchManifest] };
const lc2 = lifecycleFor(response, "research");
assert.ok(lc2, "lifecycleFor(response, 'research') resolves");
assert.equal(lc2.typeLabel, "Research");
assert.deepEqual(lc2.fieldIds, ["brief", "findings", "writeup"]);
assert.deepEqual(lc2.stateOrder, ["needs_brief", "needs_findings", "needs_writeup", "done"]);
assert.deepEqual(lc2.gatingField, {
  needs_brief: "brief",
  needs_findings: "findings",
  needs_writeup: "writeup"
});
assert.equal(advanceTargetFor(lc2, "needs_brief", "done"), "needs_findings");
assert.equal(advanceTargetFor(lc2, "needs_writeup", "done"), "done");
assert.deepEqual(ceilingOptionsFor(lc2, "needs_findings"), [
  { value: "needs_findings", label: "needs findings" },
  { value: "needs_writeup", label: "needs writeup" },
  { value: "done", label: "done" }
]);
assert.equal(fieldIsPassedFor(lc2, "brief", "needs_findings"), true);
assert.equal(fieldIsPassedFor(lc2, "findings", "needs_findings"), false);
assert.equal(gatingFieldFor(lc2, "needs_findings"), "findings");
assert.equal(recapVisibleFor(lc2, "needs_brief"), false);
assert.equal(recapVisibleFor(lc2, "needs_findings"), false);
assert.equal(recapVisibleFor(lc2, "needs_writeup"), true);
assert.equal(
  ticketStageVisualStateFor(lc2, {
    ticketState: "needs_findings",
    ticketStatus: "agent_running_step",
    fieldName: "findings"
  }),
  "current-running"
);

// The selector picks the right type: coding is unchanged, and building lc2 did not
// mutate it. (lifecycleFor(response,'coding') exercises the memo path too.)
const codingViaLookup = lifecycleFor(response, "coding");
assert.deepEqual(codingViaLookup.fieldIds, OLD_FIELD_NAMES);
assert.deepEqual(codingViaLookup.gatingField, OLD_GATING_FIELD);
assert.deepEqual(codingViaLookup.advance, OLD_ADVANCE);
assert.equal(codingViaLookup.typeLabel, "Coding");
// Memoization returns the same object on a repeat lookup.
assert.equal(lifecycleFor(response, "research"), lc2);
// An unknown type in a loaded response is null (distinct from "still loading").
assert.equal(lifecycleFor(response, "nope"), null);
assert.equal(lifecycleFor(undefined, "coding"), null);

console.log("lifecycle.test.mjs: all assertions passed");
