/** Counts describe the fictional payload, apart from the four shipped default projects. */
export interface FakeFixtureReport {
  readonly fixture_version: string;
  readonly logical_summary: Readonly<
    Record<'projects' | 'managers' | 'tickets' | 'managed_files', number>
  >;
  readonly logical_titles: Readonly<Record<'projects' | 'managers' | 'tickets', readonly string[]>>;
  readonly generated_ids: ReadonlySet<string>;
  readonly managed_file_relative_paths: readonly string[];
}
