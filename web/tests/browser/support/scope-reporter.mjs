import { writeFileSync } from "node:fs";
import { relative } from "node:path";

/** Normal test output reports this invocation; machine proof stays separate. */
export default class ScopeReporter {
  onInit(vitest) { this.vitest = vitest; }
  onTestRunEnd(modules, errors, reason) {
    const runtime = modules.filter(module => !module.meta().typecheck).map(module => ({
      path: `web/${relative(this.vitest.config.root, module.moduleId).replaceAll("\\", "/")}`,
      cases: [...module.children.allTests()].map(test => ({name: test.fullName, status: test.result()?.state || "unexecuted"})),
    }));
    const typeModules = modules.filter(module => module.meta().typecheck);
    const compiler = this.vitest.config.typecheck.enabled && typeModules.length &&
      typeModules.every(module => module.ok()) && !errors.length && reason === "passed" ? "passed" : "not-passed";
    const cases = runtime.flatMap(result => result.cases);
    const complete = runtime.length > 0 && runtime.every(result => result.cases.length > 0) &&
      cases.every(test => test.status === "passed");
    const passed = reason === "passed" && !errors.length && complete;
    const failed = reason !== "passed" || errors.length > 0 || cases.some(test => test.status === "failed");
    const outcome = passed ? "PASS" : failed ? "FAIL" : "INCOMPLETE";
    console.log(`\nPARTIAL ${outcome} — Vitest scope ${passed ? "passed" : failed ? "failed" : "has incomplete runtime proof"}`);
    console.log(`EXECUTED runtime: ${runtime.filter(result => result.cases.some(test => ["passed", "failed"].includes(test.status))).map(result => result.path).join(", ") || "none"}`);
    console.log(`UNEXECUTED runtime: ${runtime.filter(result => !result.cases.length || result.cases.some(test => !["passed", "failed"].includes(test.status))).map(result => result.path).join(", ") || "none"}`);
    console.log(`TypeScript test project: ${compiler}`);
    console.log("Runtime proof applies only to listed files; unlisted runtime tests receive no credit.");
    console.log("NOT RUN: browser entries, Python checks, Svelte checks, production build. This result is not a suite-wide pass.");
    if (process.env.PANELS_COMPANION_REPORT) {
      writeFileSync(process.env.PANELS_COMPANION_REPORT, JSON.stringify({
        schema: 1, invocationId: process.env.PANELS_BROWSER_INVOCATION,
        revision: process.env.PANELS_BROWSER_REVISION, outcome: passed ? "passed" : "failed",
        compiler, runtime,
      }, null, 2));
    }
  }
}
