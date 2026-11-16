/** Port of the four pure policy cases; no backend or browser host. */
import { describe, it, expect } from 'vitest';
import { request_headers, response_headers } from '../previews/policy.ts';
const PREFIX = '/dev/tickets/t_one/gen/';
const ORIGIN = 'https://panels.example';
const options = { request_url: ORIGIN + PREFIX, origin: ORIGIN, prefix: PREFIX };
describe('preview policy', () => {
  it('discards authority and only forwards declared cookies', () => {
    const headers = request_headers(
      [
        ['Connection', 'x-private'],
        ['X-Private', 'secret'],
        ['Tailscale-User-Login', 'owner'],
        ['X-Plan-Ticket', 't_other'],
        ['X-Forwarded-Host', 'evil'],
        ['Host', 'evil'],
        ['Cookie', 'panels=secret; demo=one'],
      ],
      { port: 3333, origin: ORIGIN, cookie_names: new Set(['demo']) },
    );
    const clean = Object.fromEntries(headers.map(([k, v]) => [k.toLowerCase(), v]));
    expect(clean.cookie).toBe('demo=one');
    expect(clean.host).toBe('127.0.0.1:3333');
    expect(clean['x-forwarded-host']).toBe('panels.example');
    for (const name of ['x-private', 'tailscale-user-login', 'x-plan-ticket'])
      expect(clean).not.toHaveProperty(name);
  });
  it('refuses redirects outside the generation', () => {
    for (const location of [
      '/login',
      'https://elsewhere.example/',
      PREFIX + '../other/',
      PREFIX + '%2e%2e/other/',
      PREFIX + 'child\\other',
      'https://PANELS.example' + PREFIX,
      'https://panels.example:443' + PREFIX,
    ])
      expect(() => response_headers([['Location', location]], options)).toThrow('redirect');
  });
  it('requires explicit generation cookie path without Domain', () => {
    for (const cookie of [
      'demo=yes',
      'demo=yes; Path=/',
      `demo=yes; Path=${PREFIX}; Domain=panels.example`,
      `__Host-demo=yes; Path=${PREFIX}`,
    ])
      expect(() => response_headers([['Set-Cookie', cookie]], options)).toThrow('cookie');
  });
  it('permits compatible redirect and cookie while removing worker scope', () => {
    const [clean, names] = response_headers(
      [
        ['Location', 'next'],
        ['Set-Cookie', `demo=yes; Path=${PREFIX}; HttpOnly`],
        ['Service-Worker-Allowed', '/'],
      ],
      options,
    );
    expect([...names]).toEqual(['demo']);
    expect(clean).toEqual([
      ['Location', 'next'],
      ['Set-Cookie', `demo=yes; Path=${PREFIX}; HttpOnly`],
    ]);
    for (const cookie of [
      `demo=yes; Path="${PREFIX}"`,
      `demo=yes; Path=${PREFIX}; Domain=""`,
      `demo=old; demo=new; Path=${PREFIX}`,
    ]) {
      const [headers, declared] = response_headers([['Set-Cookie', cookie]], options);
      expect(headers).toEqual([['Set-Cookie', cookie]]);
      expect([...declared]).toEqual(['demo']);
    }
  });
});
