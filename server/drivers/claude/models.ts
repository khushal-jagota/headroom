/** Claude model ids: the family and version rules, and alias matching. Pure. */
import type { DriverModelRules } from '../core/driver.ts';

const ALIASES = ['opus', 'sonnet', 'haiku', 'fable'];
const VERSION = /^claude-([a-z][a-z0-9]*)-(\d+(?:[-.]\d+)*)(\[1m\])?$/;

function variant(model_id: string): string | null {
  for (const alias of ALIASES)
    if (model_id === alias || model_id === alias + '[1m]') return model_id;
  const match = VERSION.exec(model_id);
  return match && match[0] === model_id ? match[1] + (match[3] || '') : null;
}
export const claude_models: DriverModelRules = {
  variant,
  version(model_id) {
    const match = VERSION.exec(model_id);
    if (!match || match[0] !== model_id) return [];
    // A date part (eight digits) is not a version part.
    return match[2]
      .split(/[-.]/)
      .filter((part) => part.length !== 8)
      .map(Number);
  },
  is_offered(requested, offered) {
    if (offered.includes(requested)) return true;
    const base_of = (model: string) => (model.endsWith('[1m]') ? model.slice(0, -4) : model);
    const base = base_of(requested);
    const listed_bases = new Set(offered.map(base_of));
    if (listed_bases.has(base)) return true;
    if (!ALIASES.includes(base)) return false;
    return [...listed_bases].some((listed) => variant(listed) === base);
  },
};
