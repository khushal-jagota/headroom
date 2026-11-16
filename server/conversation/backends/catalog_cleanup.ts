/** Catalog absence is optional. Failed native cleanup retains its distinct ownership failure. */
export class ModelCatalogCleanupFailed extends AggregateError {
  readonly primary: unknown;
  readonly cleanup_errors: readonly unknown[];
  private readonly cleanup: () => Promise<void>;
  private attempt: Promise<void> | undefined;
  private completed = false;
  constructor(
    primary: unknown,
    cleanup_errors: readonly unknown[],
    cleanup: () => Promise<void>,
    message?: string,
  ) {
    const errors = primary === undefined ? cleanup_errors : [primary, ...cleanup_errors];
    super(
      errors,
      message ??
        errors.map((error) => (error instanceof Error ? error.message : String(error))).join(': '),
    );
    this.primary = primary;
    this.cleanup_errors = cleanup_errors;
    this.cleanup = cleanup;
  }
  get pending(): boolean {
    return !this.completed;
  }
  retry_cleanup(): Promise<void> {
    if (!this.attempt) {
      const task = Promise.resolve()
        .then(this.cleanup)
        .then(() => {
          this.completed = true;
        });
      this.attempt = task;
      void task.catch(() => {
        if (this.attempt === task) this.attempt = undefined;
      });
    }
    return this.attempt;
  }
}

/** Keep each failed native close capability until a later attempt actually succeeds. */
export function remember_model_catalog_failure(history: Set<unknown>, error: unknown): void {
  if (error instanceof AggregateError && error.errors.length)
    error.errors.forEach((nested) => remember_model_catalog_failure(history, nested));
  else history.add(error);
}

export async function retry_model_catalog_cleanup(
  failures: Set<ModelCatalogCleanupFailed>,
  history = new Set<unknown>(),
  siblings: Promise<PromiseSettledResult<unknown>[]> = Promise.resolve([]),
): Promise<void> {
  const results = await Promise.allSettled(
    [...failures].map(async (failure) => {
      remember_model_catalog_failure(history, failure);
      await failure.retry_cleanup();
      failures.delete(failure);
    }),
  );
  // A sibling can own the process-close barrier that this retry releases.
  // Retain and join it even when the retry fails. A later stop can retry again.
  const sibling_results = await siblings;
  for (const result of [...results, ...sibling_results])
    if (result.status === 'rejected') remember_model_catalog_failure(history, result.reason);
  const retained = new Set([...failures].filter((failure) => failure.pending));
  const retain = (error: unknown): void => {
    if (error instanceof ModelCatalogCleanupFailed) {
      if (error.pending) retained.add(error);
    } else if (error instanceof AggregateError) error.errors.forEach(retain);
  };
  for (const result of sibling_results) if (result.status === 'rejected') retain(result.reason);
  if (history.size || retained.size)
    throw new AggregateError([...history, ...retained], 'Model catalog cleanup failed.');
}

export function model_catalog_cleanup_pending(error: unknown): boolean {
  if (error instanceof ModelCatalogCleanupFailed) return error.pending;
  return error instanceof AggregateError && error.errors.some(model_catalog_cleanup_pending);
}
