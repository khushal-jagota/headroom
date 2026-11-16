/** Explicit families and numerical version ordering require no provider I/O. */
import type { ConversationBackendKey } from '../../contracts.ts';
import type { ModelVariant } from '../contracts.ts';
const CODEX_VARIANTS = new Set(['sol', 'astra', 'luna', 'codex', 'mini', 'nano', 'pro', 'terra']);
const CODEX_VERSION = /^gpt-(\d+(?:\.\d+)*)(?:-([a-z][a-z0-9-]*))?$/;
const CLAUDE_VERSION = /^claude-([a-z][a-z0-9]*)-(\d+(?:[-.]\d+)*)(\[1m\])?$/;
export function recognize_variant(
  backend_key: ConversationBackendKey,
  model: string,
): ModelVariant | null {
  if (backend_key === 'hermes') return { backend_key, name: model };
  if (backend_key === 'codex') {
    if (CODEX_VARIANTS.has(model) || model === 'gpt') return { backend_key, name: model };
    const match = CODEX_VERSION.exec(model);
    if (match && match[0] === model) return { backend_key, name: match[2] || 'gpt' };
  }
  if (backend_key === 'claude') {
    for (const alias of ['opus', 'sonnet', 'haiku', 'fable']) {
      if (model === alias || model === alias + '[1m]') return { backend_key, name: model };
    }
    const match = CLAUDE_VERSION.exec(model);
    if (match && match[0] === model) return { backend_key, name: match[1] + (match[3] || '') };
  }
  return null;
}
export function numerical_version(model: string): readonly number[] {
  const codex = CODEX_VERSION.exec(model);
  if (codex && codex[0] === model) return codex[1].split('.').map(Number);
  const claude = CLAUDE_VERSION.exec(model);
  if (claude && claude[0] === model)
    return claude[2]
      .split(/[-.]/)
      .filter((part) => part.length !== 8)
      .map(Number);
  return [];
}
