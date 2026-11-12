import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { integerQuery, QueryIntegerError, scalarQueries } from '../list_reads/http.ts';
import { HttpError } from '../core/http/errors.ts';
import { DEFAULT_LIST_LIMIT, MAX_NAME_LENGTH } from './configuration.ts';
import type { CredentialAuthKind, CredentialSecret, CredentialWrite } from './contracts.ts';
import * as actions from './actions.ts';
import * as data from './data.ts';
import { invalid, text } from './logic/validation.ts';

function body(value: unknown, keys: string[]): Record<string, unknown> {
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).sort().join() !== keys.sort().join()
  )
    throw invalid();
  return value as Record<string, unknown>;
}
function private_body(value: unknown, keys: string[]): Record<string, unknown> {
  try {
    const raw = Buffer.isBuffer(value)
      ? new TextDecoder('utf-8', { fatal: true }).decode(value)
      : value;
    return body(typeof raw === 'string' ? JSON.parse(raw) : raw, keys);
  } catch {
    throw invalid();
  }
}
function secret(value: unknown, kind: CredentialAuthKind): CredentialSecret {
  if (kind === 'bearer') {
    const raw = body(value, ['token']);
    return { kind, token: text(raw.token) };
  }
  if (kind === 'header') {
    const raw = body(value, ['header_name', 'value']);
    return { kind, header_name: text(raw.header_name), value: text(raw.value) };
  }
  const raw = body(value, ['username', 'password']);
  return {
    kind,
    username: text(raw.username, undefined, true),
    password: text(raw.password, undefined, true),
  };
}
function revision(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1)
    throw invalid('invalid credential revision');
  return value;
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  // Python reads these private bodies itself and never reflects a malformed secret in errors.
  app.removeAllContentTypeParsers();
  app.addContentTypeParser('*', { parseAs: 'buffer' }, (_request, raw, done) => {
    done(null, raw);
  });
  const options = (request: Parameters<AreaServices['identity']>[0]) => ({
    principal: services.identity(request).principal,
    now: services.clock.nowUnix(),
    key_path: services.config.credential_key_path,
  });
  app.get<{ Querystring: { limit?: string; offset?: string } }>('/credentials', async (request) => {
    const q = scalarQueries(request.query);
    let limit = DEFAULT_LIST_LIMIT;
    let offset = 0;
    const failures: ReturnType<QueryIntegerError['payload']>[] = [];
    for (const key of ['limit', 'offset'] as const) {
      try {
        const value = integerQuery(q[key], key);
        if (value !== undefined) {
          if (key === 'limit') limit = value;
          else offset = value;
        }
      } catch (error) {
        if (error instanceof QueryIntegerError) failures.push(error.payload());
        else throw error;
      }
    }
    if (failures.length) throw new HttpError(422, failures);
    return services.database.read((sql) => data.list_metadata(sql, limit, offset));
  });
  app.post('/credentials', async (request) => {
    const settings = options(request);
    actions.require_plaintext_owner(settings.principal);
    const raw = private_body(request.body, ['scheme', 'name', 'auth_kind', 'secret']);
    if (
      raw.scheme !== 'local' ||
      typeof raw.auth_kind !== 'string' ||
      !['bearer', 'header', 'basic'].includes(raw.auth_kind)
    )
      throw invalid('unsupported credential type');
    const write: CredentialWrite = {
      reference: { scheme: 'local', name: text(raw.name, MAX_NAME_LENGTH) },
      auth_kind: raw.auth_kind as CredentialAuthKind,
      secret: secret(raw.secret, raw.auth_kind as CredentialAuthKind),
    };
    return services.database.write((tx) =>
      actions.create_credential_in_transaction(tx, write, settings),
    );
  });
  app.put<{ Params: { credential_id: string } }>(
    '/credentials/:credential_id/secret',
    async (request) => {
      const settings = options(request);
      actions.require_plaintext_owner(settings.principal);
      const raw = private_body(request.body, ['expected_revision', 'secret']);
      return services.database.write((tx) => {
        const metadata = data.read_metadata(tx, request.params.credential_id);
        return actions.replace_credential_in_transaction(
          tx,
          metadata.id,
          secret(raw.secret, metadata.auth_kind),
          revision(raw.expected_revision),
          settings,
        );
      });
    },
  );
  app.patch<{ Params: { credential_id: string } }>(
    '/credentials/:credential_id',
    async (request) => {
      const settings = options(request);
      actions.require_plaintext_owner(settings.principal);
      const raw = private_body(request.body, ['name', 'expected_revision']);
      const name = text(raw.name, MAX_NAME_LENGTH);
      const expected = revision(raw.expected_revision);
      return services.database.write((tx) =>
        actions.rename_credential(tx, request.params.credential_id, name, expected, settings),
      );
    },
  );
  app.delete<{ Params: { credential_id: string } }>(
    '/credentials/:credential_id',
    async (request) => {
      services.database.write((tx) => actions.delete_credential(tx, request.params.credential_id));
      return { deleted: true };
    },
  );
};
