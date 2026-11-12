import MarkdownIt, { type Token } from 'markdown-it';
import type { TransactionSql } from '../../core/database/contracts.ts';
const parser = new MarkdownIt({ html: true, linkify: true })
  .enable('table')
  .enable('strikethrough');
export function* markdown_hrefs(source: string): Generator<string> {
  function* walk(tokens: Token[]): Generator<string> {
    for (const token of tokens) {
      const attr = token.type === 'link_open' ? 'href' : token.type === 'image' ? 'src' : null;
      const href = attr ? token.attrGet(attr) : null;
      if (href) yield String(href);
      if (token.children) yield* walk(token.children);
    }
  }
  yield* walk(parser.parse(source, {}));
}
function split(href: string) {
  const match =
    /^(?:([a-zA-Z][a-zA-Z0-9+.-]*):)?(?:\/\/([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?$/.exec(
      href,
    );
  if (!match) throw new Error('invalid URL');
  return {
    scheme: match[1]?.toLowerCase() ?? '',
    authority: match[2] ?? '',
    path: match[3] ?? '',
    query: match[4] ?? '',
  };
}
export function canonical_origin(value: string, default_scheme = ''): string | null {
  const p = split(value);
  if (!p.authority) return null;
  const authority = p.authority.slice(p.authority.lastIndexOf('@') + 1);
  let host: string, portText: string | undefined;
  if (authority.startsWith('[')) {
    const end = authority.indexOf(']');
    if (end < 0) throw new Error('invalid IPv6 URL');
    host = authority.slice(0, end + 1).toLowerCase();
    portText = authority.slice(end + 1).replace(/^:/, '') || undefined;
  } else {
    const parts = authority.split(':');
    if (parts.length > 2) throw new Error('invalid URL port');
    host = parts[0]!.toLowerCase();
    portText = parts[1];
  }
  if (!host) return null;
  const port = portText ? Number(portText) : null;
  if (port !== null && (!/^\d+$/.test(portText!) || port > 65535))
    throw new Error('invalid URL port');
  const scheme = p.scheme || default_scheme;
  return `${scheme ? scheme + ':' : ''}//${host}${port !== null && !((scheme === 'http' && port === 80) || (scheme === 'https' && port === 443)) ? ':' + port : ''}`;
}
export function managed_href(href: string, origin: string): string | null {
  const hash = href.startsWith('/#') ? href.slice(1) : href;
  let kind: string, owner: string, path: string;
  if (hash.startsWith('#')) {
    const question = hash.indexOf('?');
    const route = question < 0 ? hash.slice(1) : hash.slice(1, question);
    const query = question < 0 ? '' : hash.slice(question + 1);
    if (route!.replace(/\/+$/, '') !== '/preview') return null;
    const params = new URLSearchParams(query);
    const source = params.get('source');
    kind =
      source === 'ticket'
        ? 'tickets'
        : source === 'manager' || source === 'sprint-item'
          ? 'managers'
          : source === 'goal'
            ? 'goals'
            : '';
    owner =
      params.get(
        kind === 'tickets'
          ? 'ticket'
          : source === 'sprint-item'
            ? 'item'
            : kind === 'goals'
              ? 'goal'
              : 'manager',
      ) ?? '';
    path = params.get('path') ?? '';
  } else {
    const p = split(href);
    if (p.scheme && !['http', 'https'].includes(p.scheme)) return null;
    if (p.authority && canonical_origin(href, split(origin).scheme) !== canonical_origin(origin))
      return null;
    if (/%(?![a-fA-F0-9]{2})/.test(p.path)) return null;
    const parts = p.path.split('/');
    if (
      parts.length < 5 ||
      parts[1] !== 'files' ||
      !['tickets', 'managers', 'sprint-items', 'goals'].includes(parts[2]!)
    )
      return null;
    kind = parts[2]!;
    try {
      owner = decodeURIComponent(parts[3]!);
      path = decodeURIComponent(parts.slice(4).join('/'));
    } catch {
      return null;
    }
  }
  if (kind === 'sprint-items') kind = 'managers';
  const expected =
    kind === 'tickets'
      ? /^t_[a-z0-9]+$/
      : kind === 'managers'
        ? /^si_[a-z0-9]+$/
        : kind === 'goals'
          ? /^goal_[a-z0-9]+$/
          : null;
  if (
    !expected?.test(owner) ||
    !path ||
    path.startsWith('/') ||
    path.includes('\\') ||
    /%(?:25|2e|2f|5c)/i.test(path) ||
    path.split('/').some((p) => ['', '.', '..'].includes(p))
  )
    return null;
  return `/files/${kind}/${owner}/` + path.split('/').map(encodeURIComponent).join('/');
}
export function finalize(sql: TransactionSql): void {
  if (!sql.get("SELECT 1 FROM sqlite_master WHERE name='ticket_artifact_link_dirty'")) return;
  while (true) {
    const rows = sql.all<{ ticket_id: string; field_id: string }>(
      'SELECT ticket_id,field_id FROM ticket_artifact_link_dirty LIMIT 64',
    );
    if (!rows.length) return;
    for (const { ticket_id, field_id } of rows) {
      const row = sql.get<{ body: string | null }>(
        field_id === '@proposal'
          ? "SELECT json_extract(pending_proposal,'$.body') AS body FROM tickets WHERE id=?"
          : 'SELECT json_extract(field_values,?) AS body FROM tickets WHERE id=?',
        field_id === '@proposal' ? [ticket_id] : ['$.' + JSON.stringify(field_id), ticket_id],
      );
      const values: {
        href: string;
        target: string;
        origin: string | null;
        stem: string;
        parent: string;
        kind: string;
      }[] = [];
      if (row?.body)
        for (const href of markdown_hrefs(row.body)) {
          try {
            const origin = canonical_origin(href);
            const target = managed_href(href, origin ?? 'http://planner.local');
            if (!target) continue;
            const path = decodeURIComponent(target).split('/').slice(4).join('/');
            const parts = path.split('/');
            const file = parts.at(-1)!;
            const dot = file.lastIndexOf('.');
            values.push({
              href,
              target,
              origin,
              stem: dot > 0 ? file.slice(0, dot) : file,
              parent: parts.at(-2) ?? '',
              kind: dot > 0 ? file.slice(dot + 1) : 'file',
            });
          } catch {
            continue;
          }
        }
      const previous = sql.all(
        'SELECT href,target,origin,stem,parent,kind FROM ticket_artifact_links WHERE ticket_id=? AND field_id=? ORDER BY ordinal',
        [ticket_id, field_id],
      );
      if (JSON.stringify(previous) !== JSON.stringify(values)) {
        sql.run('DELETE FROM ticket_artifact_links WHERE ticket_id=? AND field_id=?', [
          ticket_id,
          field_id,
        ]);
        values.forEach((v, i) =>
          sql.run('INSERT INTO ticket_artifact_links VALUES (?,?,?,?,?,?,?,?,?)', [
            ticket_id,
            field_id,
            i,
            v.href,
            v.target,
            v.origin,
            v.stem,
            v.parent,
            v.kind,
          ]),
        );
        sql.run(
          "INSERT INTO resource_source_versions VALUES ('ticket-artifact-links',?,1) ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1",
          [ticket_id],
        );
      }
      sql.run('DELETE FROM ticket_artifact_link_dirty WHERE ticket_id=? AND field_id=?', [
        ticket_id,
        field_id,
      ]);
    }
  }
}
