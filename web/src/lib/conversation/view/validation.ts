import schema from './bodySchema';
import type { ViewRowBody } from './contracts';
type Schema = {
    $ref?: string;
    anyOf?: Schema[];
    const?: unknown;
    enum?: unknown[];
    type?: string;
    required?: string[];
    properties?: Record<string, Schema>;
    items?: Schema;
    $defs?: Record<string, Schema>;
};
const root: Schema = schema;
function matches(value: unknown, shape: Schema): boolean {
    if (shape.$ref) {
        const target = root.$defs?.[shape.$ref.slice('#/$defs/'.length)];
        return target !== undefined && matches(value, target);
    }
    if (shape.anyOf)
        return shape.anyOf.some(option => matches(value, option));
    if ('const' in shape && value !== shape.const)
        return false;
    if (shape.enum && !shape.enum.includes(value))
        return false;
    switch (shape.type) {
        case 'null': return value === null;
        case 'boolean': return typeof value === 'boolean';
        case 'string': return typeof value === 'string';
        case 'number': return typeof value === 'number' && Number.isFinite(value);
        case 'integer': return typeof value === 'number' && Number.isSafeInteger(value);
        case 'array': return Array.isArray(value) && (!shape.items || value.every(item => matches(item, shape.items!)));
        case 'object': {
            if (value === null || typeof value !== 'object' || Array.isArray(value))
                return false;
            const object = value as Record<string, unknown>;
            if (shape.required?.some(key => !Object.hasOwn(object, key)))
                return false;
            return Object.entries(shape.properties ?? {}).every(([key, child]) => !Object.hasOwn(object, key) || matches(object[key], child));
        }
        default: return shape.const !== undefined || shape.enum !== undefined;
    }
}
/** Validate untrusted deferred JSON against the generated Python contract in the worker. */
export function parseViewRowBody(value: unknown): ViewRowBody {
    if (!matches(value, root))
        throw new Error('Invalid conversation row body');
    return value as ViewRowBody;
}
