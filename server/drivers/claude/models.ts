/** Claude model ids: the family and version rules, and alias matching. Pure. */
import type { DriverModelRules } from '../core/driver.ts';

const ALIASES = ['opus', 'sonnet', 'haiku', 'fable'];
const VERSION = /^claude-([a-z][a-z0-9]*)-(\d+(?:[-.]\d+)*)(\[1m\])?$/;
/** The bare aliases a resolved id can still be, named as they are. */
const NAMED_ALIAS = /^(opus|sonnet|haiku)(\[1m\])?$/i;
const capitalised = (word: string) => word[0].toUpperCase() + word.slice(1);

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
  /** As Claude's versioned display name: claude-opus-5-5 is Opus 5.5. Dates drop; [1m] is (1M). */
  readable_name(model_id) {
    const large_context = model_id.endsWith('[1m]') ? ' (1M)' : '';
    const base = large_context ? model_id.slice(0, -4) : model_id;
    const [provider, family, ...parts] = base.split('-');
    if (provider === 'claude' && family) {
      const version = parts.filter((part) => !/^\d{8}$/.test(part));
      if (version.every((part) => /^\d+$/.test(part)))
        return (
          [capitalised(family), ...(version.length ? [version.join('.')] : [])].join(' ') +
          large_context
        );
    }
    const alias = NAMED_ALIAS.exec(model_id);
    return alias ? capitalised(alias[1]) + large_context : null;
  },
};
