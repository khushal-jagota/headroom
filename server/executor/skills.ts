/** Pure canonical snapshot admission shared by the VPS sender and Mac installation. */
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import { skill_description } from '../skills/logic.ts';
import { RETIRED_PANELS_SKILL_NAMES } from '../skills/native_publication.ts';
import type { CanonicalSkillSnapshot } from './wire.generated.ts';
function invalid(message: string): never {
  throw new Error(message);
}
export function validate_skill_snapshot(snapshot: CanonicalSkillSnapshot): void {
  try {
    const names = new Set<string>(),
      folded = new Set<string>();
    for (const source of snapshot.sources) {
      const name = source.skill_name;
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]*(?![\s\S])/u.test(name)) invalid('unsafe skill name');
      if (folded.has(name.toLowerCase())) invalid('duplicate skill name');
      names.add(name);
      folded.add(name.toLowerCase());
      if (/\p{Surrogate}/u.test(source.source_text)) {
        const points = [...source.source_text],
          index = points.findIndex((point) => /\p{Surrogate}/u.test(point));
        let end = index + 1;
        while (end < points.length && /\p{Surrogate}/u.test(points[end]!)) end++;
        const position =
          end === index + 1
            ? `character '\\u${points[index]!.charCodeAt(0).toString(16).padStart(4, '0')}' in position ${index}`
            : `characters in position ${index}-${end - 1}`;
        invalid(`'utf-8' codec can't encode ${position}: surrogates not allowed`);
      }
      skill_description(source.source_text, name);
    }
    const required = snapshot.required_skill_names;
    if (new Set(required).size !== required.length) invalid('duplicate required skill name');
    if (!required.includes('panels') || !required.includes('panels-worker'))
      invalid('base Worker skills are not required');
    if (required.some((name) => RETIRED_PANELS_SKILL_NAMES.has(name)))
      invalid('required canonical skill is retired');
    if (required.some((name) => !names.has(name))) invalid('required canonical skill is absent');
  } catch (error) {
    throw new BackendSpawnFailed(
      `invalid canonical skill snapshot: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
