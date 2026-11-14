/** Kernel evidence and bypass rules. No filesystem or transport operations. */
import { TYPESCRIPT_SERVICE_NAME } from '../../lifecycle/service_identity.ts';
import { InvalidPeerEvidence } from './contracts.ts';
import type {
  UnidentifiedCallRequest,
  UnidentifiedHttpRefusal,
  UnidentifiedWebSocketRefusal,
} from './contracts.ts';

const REFUSAL = 'Unidentified caller refused. Restore PLAN_ACTOR and your Panels identity.';
export const words = (value: string): string[] => value.trim().split(/[ \t]+/);
export function splitlines(text: string): string[] {
  if (!text) return [];
  const lines = text.split(/\r?\n/);
  if (lines.at(-1) === '') lines.pop();
  return lines;
}
export function agent_cgroup(text: string): boolean {
  const lines = splitlines(text);
  if (!lines.length) throw new InvalidPeerEvidence('missing cgroup evidence');
  for (const line of lines) {
    const first = line.indexOf(':'),
      second = line.indexOf(':', first + 1);
    if (first < 0 || second < 0 || line[second + 1] !== '/')
      throw new InvalidPeerEvidence('invalid cgroup evidence');
    // Path.parts retains '..'. Do not resolve the kernel's component evidence.
    const parts = line.slice(second + 1).split('/');
    if (
      parts.some(
        (part) =>
          part === 'panels-live.service' ||
          part === 'panels-workers.slice' ||
          part === TYPESCRIPT_SERVICE_NAME,
      )
    )
      return true;
  }
  return false;
}
function ipv4(text: string): number[] | null {
  const parts = text.split('.');
  if (
    parts.length !== 4 ||
    parts.some((part) => !/^(?:0|[1-9]\d{0,2})$/.test(part) || Number(part) > 255)
  )
    return null;
  return parts.map(Number);
}
function normalize(bytes: number[]): number[] {
  return bytes.length === 16 &&
    bytes.slice(0, 10).every((part) => part === 0) &&
    bytes[10] === 255 &&
    bytes[11] === 255
    ? bytes.slice(12)
    : bytes;
}
export function address_bytes(text: string): number[] | null {
  const v4 = ipv4(text);
  if (v4) return v4;
  const scope = text.split('%');
  if (scope.length > 2 || (scope.length === 2 && !scope[1])) return null;
  let value = scope[0];
  if (value.includes('.')) {
    const colon = value.lastIndexOf(':'),
      tail = ipv4(value.slice(colon + 1));
    if (!tail) return null;
    value =
      value.slice(0, colon + 1) +
      ((tail[0] << 8) | tail[1]).toString(16) +
      ':' +
      ((tail[2] << 8) | tail[3]).toString(16);
  }
  const halves = value.split('::');
  if (halves.length > 2) return null;
  const parts = (half: string) => (half ? half.split(':') : []);
  const left = parts(halves[0]),
    right = halves.length === 2 ? parts(halves[1]) : [];
  if ([...left, ...right].some((part) => !/^[0-9a-fA-F]{1,4}$/.test(part))) return null;
  const missing = 8 - left.length - right.length;
  if (halves.length === 1 ? missing !== 0 : missing <= 0) return null;
  const words = [...left, ...Array<string>(missing).fill('0'), ...right];
  return normalize(
    words.flatMap((part) => {
      const word = Number.parseInt(part, 16);
      return [word >> 8, word & 255];
    }),
  );
}
export function proc_address(encoded: string, little_endian: boolean): number[] {
  if (!/^(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{32})$/.test(encoded))
    throw new InvalidPeerEvidence('invalid socket address');
  const bytes = encoded.match(/../g)!.map((part) => Number.parseInt(part, 16));
  if (little_endian)
    for (let offset = 0; offset < bytes.length; offset += 4)
      bytes.splice(offset, 4, ...bytes.slice(offset, offset + 4).reverse());
  return normalize(bytes);
}
export function integer(value: string | undefined, radix = 10): bigint {
  const pattern = radix === 16 ? /^[0-9a-fA-F]+$/ : /^[0-9]+$/;
  if (value === undefined || !pattern.test(value))
    throw new InvalidPeerEvidence('invalid socket integer');
  return BigInt(radix === 16 ? '0x' + value : value);
}
export function lookup_required(enabled: boolean, request: UnidentifiedCallRequest): boolean {
  if (!enabled || request.kind === 'other' || request.authenticated_owner) return false;
  const first = request.headers.find(([name]) => name.toLowerCase() === 'x-plan-actor');
  if (first && first[1].trim()) return false;
  if (request.client !== null) {
    const address = address_bytes(request.client[0]);
    if (address !== null) {
      const loopback =
        address.length === 4
          ? address[0] === 127
          : address.slice(0, 15).every((part) => part === 0) && address[15] === 1;
      if (!loopback) return false;
    }
  }
  return true;
}
export function http_refusal(skipped: number): UnidentifiedHttpRefusal {
  return {
    status: 403,
    body: {
      error: {
        code: 'agent_forbidden',
        message: REFUSAL,
        detail: { skipped_unreadable_processes: skipped },
      },
    },
  };
}
export function websocket_refusal(): UnidentifiedWebSocketRefusal {
  return { type: 'websocket.close', code: 1008, reason: REFUSAL };
}
