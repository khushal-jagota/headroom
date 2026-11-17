/** Codex model ids: the family and version rules, and the launch refusal. Pure. */
import type { DriverModelRules } from '../core/driver.ts';

const VARIANTS = new Set(['sol', 'astra', 'luna', 'codex', 'mini', 'nano', 'pro', 'terra']);
const VERSION = /^gpt-(\d+(?:\.\d+)*)(?:-([a-z][a-z0-9-]*))?$/;

export const codex_models: DriverModelRules = {
  variant(model_id) {
    if (VARIANTS.has(model_id) || model_id === 'gpt') return model_id;
    const match = VERSION.exec(model_id);
    return match && match[0] === model_id ? match[2] || 'gpt' : null;
  },
  version(model_id) {
    const match = VERSION.exec(model_id);
    return match && match[0] === model_id ? match[1].split('.').map(Number) : [];
  },
};
export function codex_launch_model_refusal(model: string): string | null {
  return model === 'sol'
    ? 'The legacy Codex model "sol" cannot run. Choose an explicit Codex model ' +
        "in this Manager's model settings before you retry."
    : null;
}
