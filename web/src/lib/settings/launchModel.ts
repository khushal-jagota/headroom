import { resolveModelPicker } from "../conversation/modelPicker";
import type { BackendSnapshot } from "../conversation/wire";
import type { DriverPresentation } from "../drivers/contracts";
import type { EmployeeConfigurationSnapshot } from "../types";

/** An agent's launch model as a list row shows it: "Opus 5.5 · medium".
 *
 * The words are the model picker's own face, so a list row and the picker on its detail
 * page never disagree. Before the backend catalogue arrives the model id stands in for
 * its name; with no model saved at all, the backend's name does.
 */
export function launchModelText(
  value: EmployeeConfigurationSnapshot,
  backends: readonly BackendSnapshot[],
  drivers: readonly DriverPresentation[] | undefined
): string {
  const view = resolveModelPicker({
    backendKey: value.employee_backend,
    drivers,
    model: value.employee_launch_model,
    reasoningEffort: value.employee_launch_reasoning_effort,
    backends
  });
  return [view.modelFace || view.backendName, view.effortFace].filter(Boolean).join(" · ");
}
