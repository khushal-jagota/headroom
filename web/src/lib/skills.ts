import { mutateJsonAndWaitForScreen } from "./mutate";
import type { ManagedSkill, ManagedSkillField, ManagedSkillPatch } from "./types";

export async function saveSkillField(
  skillName: string,
  field: ManagedSkillField,
  raw: string
): Promise<void> {
  const body: ManagedSkillPatch = { [field]: raw };
  await mutateJsonAndWaitForScreen<ManagedSkill>(`/api/skills/${encodeURIComponent(skillName)}`, {
    method: "PATCH",
    body
  });
}
