import { PlannerError } from '../../core/contracts.ts';
export function require_ticket_locale(raw: unknown): 'vps' | 'macos' { if (raw === 'vps' || raw === 'macos')
    return raw; throw new PlannerError('validation', 'locale must be vps or macos', { locale: typeof raw === 'string' ? raw : raw === null ? 'None' : typeof raw === 'boolean' ? (raw ? 'True' : 'False') : String(raw) }); }
export function resolve_ticket_locale(explicit: string | null): 'vps' | 'macos' { return explicit === null ? 'vps' : require_ticket_locale(explicit); }
