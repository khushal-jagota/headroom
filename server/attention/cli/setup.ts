/** Bind the existing collection orchestration through the CLI transport. */
import { JsonNumber } from '../../core/http/numeric_json.ts';
import type { Invocation } from '../../cli/contracts.ts';
import { send, fail_validation, json } from '../../cli/http.ts';
import { CliFailure } from '../../cli/errors.ts';
export async function setup_collection(inv: Invocation): Promise<unknown> {
  const { setup_collection: setup } = await import('../setup.ts');
  try {
    return await setup(
      (method, path, options) =>
        send(inv, method, path, {
          ...options,
          params: options?.params
            ? Object.fromEntries(
                Object.entries(options.params).map(([name, value]) => [
                  name,
                  (value as unknown) instanceof JsonNumber ? json(value) : value,
                ]),
              )
            : undefined,
        }),
      {
        project_id: inv.values.project_id,
        database_path: inv.values.database_path,
        local_time: inv.values.local_time,
      },
    );
  } catch (error) {
    if (error instanceof CliFailure) throw error;
    if (
      error instanceof Error &&
      [
        'attention collection needs an absolute server database path',
        'multiple attention sources need explicit repair',
        'duplicate attention outputs need explicit repair',
      ].includes(error.message)
    )
      fail_validation(inv, error.message);
    throw error;
  }
}
