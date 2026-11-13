/** Catalog absence is optional. Failed native cleanup retains its distinct ownership failure. */
export class ModelCatalogCleanupFailed extends AggregateError {
  readonly primary: unknown;
  readonly cleanup_errors: readonly unknown[];
  private readonly cleanup: () => Promise<void>;
  private attempt: Promise<void> | undefined;
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
  retry_cleanup(): Promise<void> {
    if (!this.attempt) {
      const task = Promise.resolve().then(this.cleanup);
      this.attempt = task;
      void task.catch(() => {
        if (this.attempt === task) this.attempt = undefined;
      });
    }
    return this.attempt;
  }
}

/** Keep each failed native close capability until a later attempt actually succeeds. */
export async function retry_model_catalog_cleanup(
  failures: Set<ModelCatalogCleanupFailed>,
): Promise<void> {
  const results = await Promise.allSettled(
    [...failures].map(async (failure) => {
      await failure.retry_cleanup();
      failures.delete(failure);
    }),
  );
  const failed = results.filter((result) => result.status === 'rejected');
  if (failed.length)
    throw new AggregateError(
      [...new Set([...failures, ...failed.map((result) => result.reason)])],
      'Model catalog cleanup remains unfinished.',
    );
}
