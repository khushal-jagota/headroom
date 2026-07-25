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
const uiModule = await import(join(dir, "ui.mjs"));
const {
  buildLifecycle,
  lifecycleFor,
  gatingFieldFor,
  advanceTargetFor,
  ceilingOptionsFor,
  fieldIsPassedFor,
  ticketStageVisualStateFor,
  fieldStageVisualStateFor
} = await import(lifecyclePath);
await rm(dir, { recursive: true, force: true });

const { ticketStatusText } = uiModule;

// lifecycleFor is imported above from the REAL lifecycle.ts (not a copy) so Part B
// exercises the production selector + memoization (Codex F6).

// --- the coding manifest literal, exactly as GET /api/worker-types serves it -----
const codingManifest = {
  worker_type: "coding",
  label: "Coding",
  stages: [
    {
      id: "needs_kickoff",
      label: "Kickoff",
      gating_field: "kickoff",
      is_terminal: false,
      default_ownership_mode: "worker"
    },
    {
      id: "needs_success",
      label: "Success",
      gating_field: "success",
      is_terminal: false,
      default_ownership_mode: "worker"
    },
    {
      id: "needs_approach",
      label: "Approach",
      gating_field: "approach",
      is_terminal: false,
      default_ownership_mode: "user"
    },
    {
      id: "needs_plan",
      label: "Plan",
      gating_field: "plan",
      is_terminal: false,
      default_ownership_mode: "paired"
    },
    {
      id: "needs_implementation",
      label: "Implementation",
      gating_field: "implementation",
      is_terminal: false,
      default_ownership_mode: "worker"
    },
    {
      id: "needs_closeout",
      label: "Closeout",
      gating_field: "closeout",
      is_terminal: false,
      default_ownership_mode: "worker"
    },
    { id: "done", label: "Done", gating_field: null, is_terminal: true, default_ownership_mode: null }
  ],
  dropped: {
    id: "dropped",
    label: "Dropped",
    gating_field: null,
    is_terminal: true,
    default_ownership_mode: null
  },
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
  worker_profile_id: "panels-worker-coding",
  default_employee_backend: "hermes"
};

// --- Part A: coding byte-identity vs a HARDCODED copy of today's ui.ts maps ------
// These are the retired constants, hardcoded here (NOT derived from the manifest) —
// the assertion is that the manifest-built lifecycle reproduces them exactly.
const OLD_FIELD_NAMES = ["kickoff", "success", "approach", "plan", "implementation", "closeout"];
const OLD_STAGE_ORDER = [
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
assert.equal(coding.workerType, "coding");
assert.deepEqual(coding.fieldIds, OLD_FIELD_NAMES, "coding fieldIds == old FIELD_NAMES");
assert.deepEqual(coding.stageOrder, OLD_STAGE_ORDER, "coding stageOrder == old STAGE_ORDER");
assert.deepEqual(coding.gatingField, OLD_GATING_FIELD, "coding gatingField == old GATING_FIELD");
assert.deepEqual(coding.gatedStage, {
  kickoff: "needs_kickoff",
  success: "needs_success",
  approach: "needs_approach",
  plan: "needs_plan",
  implementation: "needs_implementation",
  closeout: "needs_closeout"
});
assert.deepEqual(coding.advance, OLD_ADVANCE, "coding advance == old ADVANCE");
assert.equal(coding.workerTypeLabel, "Coding");
assert.deepEqual(coding.stageDefaultOwnershipMode, {
  needs_kickoff: "worker",
  needs_success: "worker",
  needs_approach: "user",
  needs_plan: "paired",
  needs_implementation: "worker",
  needs_closeout: "worker",
  done: null
});

// The scope leash options keep the LOWERCASE stageLabel(id) labels ("needs success"),
// NOT the manifest's capitalized stage.label — the mockup wording must not change.
assert.deepEqual(ceilingOptionsFor(coding, "needs_success"), [
  { value: "needs_success", label: "needs success" },
  { value: "needs_approach", label: "needs approach" },
  { value: "needs_plan", label: "needs plan" },
  { value: "needs_implementation", label: "needs implementation" },
  { value: "needs_closeout", label: "needs closeout" },
  { value: "done", label: "done" }
]);
// A mid-range floor slices from that Stage.
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
    ticketStage: "needs_success",
    ticketStatus: "agent_running_step",
    fieldName: "success"
  }),
  "current-running"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_success",
    ticketStatus: "errored",
    fieldName: "success"
  }),
  "errored"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_success",
    ticketStatus: "awaiting_approval",
    fieldName: "success"
  }),
  "current-awaiting-approval"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_success",
    ticketStatus: "proposal_discussion",
    fieldName: "success"
  }),
  "current-awaiting-approval"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_success",
    ticketStatus: "empty",
    fieldName: "success",
    fieldHasProposal: true
  }),
  "current-awaiting-approval"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_success",
    ticketStatus: "empty",
    fieldName: "success"
  }),
  "current-waiting"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_success",
    ticketStatus: "paired_work",
    fieldName: "success"
  }),
  "current-paired-work"
);
assert.equal(ticketStatusText("paired_work"), "paired work");
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_approach",
    ticketStatus: "empty",
    fieldName: "success"
  }),
  "completed"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "done",
    ticketStatus: "empty",
    fieldName: "closeout"
  }),
  "completed"
);
assert.equal(
  ticketStageVisualStateFor(coding, {
    ticketStage: "needs_success",
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
      stage: "needs_success",
      ticket_status: "empty",
      fields: { success: { proposal: { body: "x", proposed_by: "worker" } } }
    },
    "success"
  ),
  "current-awaiting-approval"
);

// --- Part A: pre-load null defaults (Codex F1) ----------------------------------
assert.equal(gatingFieldFor(null, "needs_success"), null);
assert.equal(advanceTargetFor(null, "needs_plan", "done"), null);
assert.deepEqual(ceilingOptionsFor(null, "needs_success"), []);
assert.equal(fieldIsPassedFor(null, "success", "needs_approach"), false);
assert.equal(
  ticketStageVisualStateFor(null, {
    ticketStage: "needs_success",
    ticketStatus: "agent_running_step",
    fieldName: "success"
  }),
  "upcoming"
);

// --- Part B: a synthetic SECOND Worker type proves the render logic is variable ---
// research: needs_brief -> needs_findings -> needs_writeup -> done; fields brief/
// findings/writeup. Routed through the real lookup+memoize (lifecycleFor), NOT
// buildLifecycle(research) directly (Codex F6).
const researchManifest = {
  worker_type: "research",
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
  worker_profile_id: "research-worker",
  default_employee_backend: "probe-backend"
};

const response = {
  employee_backends: ["hermes", "probe-backend"],
  worker_types: [codingManifest, researchManifest]
};
assert.deepEqual(response.employee_backends, ["hermes", "probe-backend"]);
assert.equal(codingManifest.default_employee_backend, "hermes");
assert.equal(researchManifest.default_employee_backend, "probe-backend");
const lc2 = lifecycleFor(response, "research");
assert.ok(lc2, "lifecycleFor(response, 'research') resolves");
assert.equal(lc2.workerType, "research");
assert.equal(lc2.workerTypeLabel, "Research");
assert.deepEqual(lc2.fieldIds, ["brief", "findings", "writeup"]);
assert.deepEqual(lc2.stageOrder, ["needs_brief", "needs_findings", "needs_writeup", "done"]);
assert.deepEqual(lc2.gatingField, {
  needs_brief: "brief",
  needs_findings: "findings",
  needs_writeup: "writeup"
});
assert.deepEqual(lc2.gatedStage, {
  brief: "needs_brief",
  findings: "needs_findings",
  writeup: "needs_writeup"
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
assert.equal(
  ticketStageVisualStateFor(lc2, {
    ticketStage: "needs_findings",
    ticketStatus: "agent_running_step",
    fieldName: "findings"
  }),
  "current-running"
);

// The selector picks the right Worker type: coding is unchanged, and building lc2 did not
// mutate it. (lifecycleFor(response,'coding') exercises the memo path too.)
const codingViaLookup = lifecycleFor(response, "coding");
assert.deepEqual(codingViaLookup.fieldIds, OLD_FIELD_NAMES);
assert.deepEqual(codingViaLookup.gatingField, OLD_GATING_FIELD);
assert.deepEqual(codingViaLookup.advance, OLD_ADVANCE);
assert.equal(codingViaLookup.workerTypeLabel, "Coding");
// Memoization returns the same object on a repeat lookup.
assert.equal(lifecycleFor(response, "research"), lc2);
// An unknown Worker type in a loaded response is null (distinct from "still loading").
assert.equal(lifecycleFor(response, "nope"), null);
assert.equal(lifecycleFor(undefined, "coding"), null);

console.log("lifecycle.test.mjs: all assertions passed");
