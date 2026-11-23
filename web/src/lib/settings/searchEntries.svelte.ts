import { createQuery } from "@tanstack/svelte-query";
import { queries } from "../queryCatalogue";
import { goalSidebarRows } from "../goals/sidebarRows.svelte";
import { launchModelText } from "./launchModel";
import { backendLabel } from "../drivers/presentation";
import { buildSettingsSearchEntries, type SettingsSearchEntry } from "./searchIndex";
import { backendStatus, machineStatus } from "./statusText";

/** The Diagnostics panels, by the key each one's rowId uses. */
const DIAGNOSTICS_PANELS = [
  { key: "cpu", label: "CPU" }, { key: "memory", label: "Memory" }, { key: "load", label: "Load" },
  { key: "io", label: "Disk I/O" }, { key: "network", label: "Network" }, { key: "disk", label: "Disk" },
  { key: "services", label: "Services" }, { key: "processes", label: "Processes" }
];

/** Every Settings row that search can find, from the reads the pages already make.
 *  The reads share the pages' cache and run only while `active` is true, so Settings makes
 *  no extra request until someone searches. */
export function useSettingsSearchEntries(active: () => boolean): { readonly current: SettingsSearchEntry[] } {
  const notifications = createQuery(() => ({ ...queries.notificationSettings(), enabled: active() }));
  const workers = createQuery(() => ({ ...queries.workers(), enabled: active() }));
  const managerDefaults = createQuery(() => ({ ...queries.managerLaunchDefaults(), enabled: active() }));
  const skills = createQuery(() => ({ ...queries.skillsHome(), enabled: active() }));
  const manifests = createQuery(() => ({ ...queries.supportedWorkerTypeManifests(), enabled: active() }));
  const machines = createQuery(() => ({ ...queries.machines(), enabled: active() }));
  const backends = createQuery(() => ({ ...queries.backends(), enabled: active() }));
  const drivers = createQuery(() => ({ ...queries.drivers(), enabled: active() }));

  const entries = $derived.by(() => {
    const backendList = backends.data ?? [];
    const driverList = drivers.data?.drivers;
    const skillsByName = new Map((skills.data?.skills ?? []).map(skill => [skill.name, skill]));
    const chief = workers.data?.chief_of_staff;
    const agents = [];
    if (chief) {
      const value = launchModelText(chief.launch_defaults, backendList, driverList);
      agents.push({ id: "chief-of-staff", name: chief.label, value, model: value, description: chief.skill.description });
    }
    const manager = skillsByName.get("panels-manager");
    if (manager) {
      const value = managerDefaults.data ? launchModelText(managerDefaults.data, backendList, driverList) : "";
      agents.push({ id: "manager", name: "Manager", value, model: value, description: manager.description });
    }
    const workerSkill = skillsByName.get("panels-worker");
    if (workerSkill) agents.push({ id: "worker-skill", name: "Worker skill", value: "Shared by every Worker", description: workerSkill.description });
    const stagesByType = new Map((manifests.data?.worker_types ?? []).map(item => [item.worker_type, item.stages.length]));
    const checks = (driverList ?? []).filter(driver => driver.checks_for_updates).map(driver => driver.driver_kind);
    for (const snapshot of backendList) if (snapshot.can_sign_in && !checks.includes(snapshot.backend_key)) checks.push(snapshot.backend_key);
    return buildSettingsSearchEntries({
      goalSidebarRows: goalSidebarRows.on,
      notificationSubjects: notifications.data?.subjects,
      agents: workers.data || skills.data ? agents : undefined,
      workers: workers.data?.workers.map(item => ({
        workerType: item.worker_type, label: item.label,
        value: launchModelText(item.launch_defaults, backendList, driverList),
        stages: stagesByType.has(item.worker_type) ? `${stagesByType.get(item.worker_type)} stages` : undefined
      })),
      machines: machines.data?.machines.map(machine => ({ machineId: machine.machine_id, name: machine.name, status: machineStatus(machine).text })),
      backends: checks.map(key => ({
        key, name: backendLabel(key, driverList),
        status: backends.data ? backendStatus(backendList.find(snapshot => snapshot.backend_key === key), { refreshing: false, refreshError: null }) : ""
      })),
      diagnostics: DIAGNOSTICS_PANELS
    });
  });
  return { get current() { return entries; } };
}
