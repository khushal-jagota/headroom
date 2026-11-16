/** A retained reason is reported when it first appears or changes. */
export class RetainedWorkspaceReporter {
  private reported = new Set<string>();
  report(skipped: readonly { path: string; reason: string }[]): void {
    const current = new Set(skipped.map((item) => JSON.stringify([item.path, item.reason])));
    for (const entry of [...current].sort()) {
      if (this.reported.has(entry)) continue;
      const [path, reason] = JSON.parse(entry) as [string, string];
      console.warn(`Ticket workspace retained: ${path} (${reason})`);
    }
    this.reported = current;
  }
}
