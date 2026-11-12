/** Ask the app-server for models. Catalog presence does not establish entitlement. */
import { homedir } from 'node:os';
import { CodexAppServerClient, CodexAppServerError } from './client.ts';
import { CodexModelCatalogUnavailable } from '../../snapshot/provider_contracts.ts';
import type { CodexModelCatalog, CodexModel } from '../../snapshot/provider_contracts.ts';
import { child_environment } from './environment.ts';
import * as P from './protocol.ts';
import {
  CLIENT_NAME,
  CLIENT_TITLE,
  CLIENT_VERSION,
  INITIALIZE_CAPABILITIES,
} from './adapter_configuration.ts';
export { CodexModelCatalogUnavailable } from '../../snapshot/provider_contracts.ts';
export const MODEL_CATALOG_TIMEOUT_SECONDS = 30;
type CatalogClient = Pick<CodexAppServerClient, 'start' | 'stop' | 'request' | 'notify'>;
export interface ModelCatalogOptions {
  signal?: AbortSignal;
  client_factory?: (
    options: ConstructorParameters<typeof CodexAppServerClient>[0],
  ) => CatalogClient;
}
export async function probe_codex_model_catalog(
  executable: string,
  options: ModelCatalogOptions = {},
): Promise<CodexModelCatalog> {
  options.signal?.throwIfAborted();
  const client = (options.client_factory ?? ((config) => new CodexAppServerClient(config)))({
    description: 'model-catalog',
    handler: {
      async on_notification() {},
      async on_server_request() {},
      async on_child_ended() {},
    },
  });
  const cancel = new AbortController();
  const abort = () => cancel.abort(options.signal!.reason);
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  let timed_out = false;
  const timeout = setTimeout(() => {
    timed_out = true;
    cancel.abort(
      new CodexModelCatalogUnavailable(
        `codex did not answer within ${MODEL_CATALOG_TIMEOUT_SECONDS}s`,
      ),
    );
  }, MODEL_CATALOG_TIMEOUT_SECONDS * 1000);
  const canceled = new Promise<never>((_, reject) => {
    if (cancel.signal.aborted) reject(cancel.signal.reason);
    else
      cancel.signal.addEventListener('abort', () => reject(cancel.signal.reason), { once: true });
  });
  const operation = (async () => {
    const pages: P.CatalogModel[] = [];
    let complete = false;
    cancel.signal.throwIfAborted();
    await client.start({
      argv: [executable, 'app-server'],
      environment: child_environment(),
      working_directory: homedir(),
    });
    cancel.signal.throwIfAborted();
    await client.request(
      'initialize',
      {
        clientInfo: { name: CLIENT_NAME, title: CLIENT_TITLE, version: CLIENT_VERSION },
        capabilities: INITIALIZE_CAPABILITIES,
      },
      { signal: cancel.signal },
    );
    cancel.signal.throwIfAborted();
    await client.notify('initialized');
    let cursor: string | null = null;
    const seen = new Set<string>();
    while (true) {
      cancel.signal.throwIfAborted();
      let listed: P.ModelListResponse;
      try {
        listed = P.ModelListResponse.read(
          await client.request('model/list', cursor === null ? {} : { cursor }, {
            signal: cancel.signal,
          }),
        );
      } catch (error) {
        if (
          (error instanceof CodexAppServerError || error instanceof P.CodexProtocolError) &&
          pages.length
        )
          break;
        throw error;
      }
      pages.push(...listed.data);
      cursor = listed.nextCursor;
      if (cursor === null) {
        complete = true;
        break;
      }
      if (seen.has(cursor)) break;
      seen.add(cursor);
    }
    return codex_model_catalog({ data: pages, nextCursor: null }, complete);
  })();
  try {
    return await Promise.race([operation, canceled]);
  } catch (error) {
    if (timed_out)
      throw new CodexModelCatalogUnavailable(
        `codex did not answer within ${MODEL_CATALOG_TIMEOUT_SECONDS}s`,
      );
    if (error instanceof CodexAppServerError || error instanceof P.CodexProtocolError)
      throw new CodexModelCatalogUnavailable(error.message);
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
    try {
      await client.stop();
    } finally {
      await operation.catch(() => {});
    }
  }
}
export function codex_model_catalog(
  listed: P.ModelListResponse,
  complete = true,
): CodexModelCatalog {
  const models: CodexModel[] = [];
  const efforts: string[] = [];
  for (const model of listed.data) {
    if (model.hidden) continue;
    const offered = model.supportedReasoningEfforts.map((option) => option.reasoningEffort);
    models.push({
      model_id: model.model,
      display_name: model.displayName,
      reasoning_effort_options: offered,
      default_reasoning_effort: model.defaultReasoningEffort,
      is_default: model.isDefault,
    });
    for (const effort of offered) if (!efforts.includes(effort)) efforts.push(effort);
  }
  const selected = models.find((model) => model.is_default);
  return {
    models,
    complete,
    reasoning_effort_options: efforts,
    default_model_id: selected?.model_id ?? null,
    default_reasoning_effort: selected?.default_reasoning_effort ?? null,
  };
}
