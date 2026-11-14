/** Native command declarations. Runtime handlers load only after selection. */
import {
  CliCommand,
  argument,
  option,
  text,
  boolean,
  uuid,
  integer,
  choice,
  readable_path,
} from './declarations.ts';

export function create_commands(environment: NodeJS.ProcessEnv = {}): CliCommand {
  const root = new CliCommand([], 'Operate the local planner server.');
  const by_path = new Map<string, CliCommand>([['', root]]);
  const command = (
    path: string,
    description: string,
    inputs: Parameters<CliCommand['addInputs']>[0] = [],
    hidden = false,
  ) => {
    const words = path.split(' ');
    const current = new CliCommand(words, description).addInputs(inputs, environment);
    by_path.get(words.slice(0, -1).join(' '))!.addCommand(current, { hidden });
    by_path.set(path, current);
  };
  command('attention', 'Read where owner involvement went over selected dates.');
  command(
    'attention setup-collection',
    'Configure daily retained figures through existing source and measure APIs.',
    [
      option('project_id', '--project <TEXT>', text, { required: true }),
      option('database_path', '--database-path <PATH>', readable_path('any', false), {
        description: 'Absolute database path on the Panels server, read-only during collection.',
        required: true,
      }),
      option('local_time', '--local-time <TEXT>', text, { default: '00:05' }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'attention summary',
    'Compare recorded involvement with the preceding date range.\n\nOmit both dates for the last seven local dates. Filters select one work scope;\n--group-by selects its breakdown. These figures do not measure owner hours.',
    [
      option('first_date', '--first-date <TEXT>', text, {
        description: 'First inclusive local date (YYYY-MM-DD).',
      }),
      option('last_date', '--last-date <TEXT>', text, {
        description: 'Last inclusive local date (YYYY-MM-DD).',
      }),
      option('group_by', '--group-by <CHOICE>', choice(['project', 'manager', 'ticket', 'goal']), {
        default: 'project',
      }),
      option('project_id', '--project <TEXT>', text, {}),
      option('manager_id', '--manager <TEXT>', text, {}),
      option('ticket_id', '--ticket <TEXT>', text, {}),
      option('goal_id', '--goal <TEXT>', text, {}),
      option('chief', '--chief', boolean, { default: false }),
      option(
        'sort',
        '--sort <CHOICE>',
        choice(['owner_messages', 'owner_approvals', 'approval_wait_seconds']),
        { default: 'owner_messages' },
      ),
      option('limit', '--limit <INTEGER>', integer(), { default: 50 }),
      option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('credential', 'Discover credential references. Secret writes use the owner API.');
  command('credential list', 'List reusable names and metadata without secret values.', [
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'credential rename',
    'Rename a reusable credential as the owner without exposing its value.',
    [
      argument('credential_id', '<CREDENTIAL_ID>', text, {}),
      option('name', '--name <TEXT>', text, { required: true }),
      option('expected_revision', '--expected-revision <INTEGER>', integer(1), { required: true }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'credential request',
    "Request a named API key in this employee's conversation. No secret input.",
    [
      option('request_id', '--request-id <UUID>', uuid, { required: true }),
      option('name', '--name <TEXT>', text, { required: true }),
      option('provider', '--provider <TEXT>', text, { required: true }),
      option('use_context', '--use <TEXT>', text, { required: true }),
      option('auth_kind', '--auth-kind <CHOICE>', choice(['bearer', 'header']), { required: true }),
      option('header_name', '--header-name <TEXT>', text, {}),
      option('help_hint', '--help-text <TEXT>', text, {}),
      option('help_url', '--help-url <TEXT>', text, {}),
      option('replace_id', '--replace-id <TEXT>', text, {}),
      option('expected_revision', '--expected-revision <INTEGER>', integer(1), {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('dataset', 'Define datasets and read their retained dated rows.');
  command('dataset create', 'Read the complete dataset definition as JSON from stdin.', [
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('dataset edit', 'Read changes and expected_revision as JSON from stdin.', [
    argument('dataset_id', '<DATASET_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('dataset list', 'List a bounded page of complete definitions.', [
    option('project_id', '--project-id <TEXT>', text, {}),
    option('source_id', '--source-id <TEXT>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('dataset read', 'Read {dataset_ids, first_date, last_date, limit?} JSON from stdin.', [
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('dataset show', 'Show one complete definition.', [
    argument('dataset_id', '<DATASET_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('environment', 'Prepare and run isolated Panels environments.', [], true);
  command('environment app-build', 'Build and validate one host-native exact-SHA app.', [
    option('source_root', '--source-root <DIRECTORY>', readable_path('directory', true), {
      required: true,
    }),
    option('requested_sha', '--requested-sha <TEXT>', text, { required: true }),
    option('candidate_app', '--candidate-app <DIRECTORY>', readable_path('directory', false), {
      required: true,
    }),
  ]);
  command('environment app-identity', 'Print the validated SHA for one app directory.', [
    option('app', '--app <DIRECTORY>', readable_path('directory', true), { required: true }),
  ]);
  command(
    'environment backup',
    'Create one verified snapshot of the database and the managed-file tree.',
    [
      option('source_db', '--source-db <FILE>', readable_path('file', true), { required: true }),
      option('backup_dir', '--backup-dir <DIRECTORY>', readable_path('directory', false), {
        required: true,
      }),
      option('deployed_revision', '--deployed-revision <TEXT>', text, { required: true }),
    ],
  );
  command(
    'environment backup-current',
    'Back up the database and files even when the installed app files changed.',
    [
      option('source_db', '--source-db <FILE>', readable_path('file', true), { required: true }),
      option('backup_dir', '--backup-dir <DIRECTORY>', readable_path('directory', false), {
        required: true,
      }),
      option('current_app', '--current-app <DIRECTORY>', readable_path('directory', true), {
        required: true,
      }),
    ],
  );
  command(
    'environment cleanup',
    'Inspect or safely maintain host-local Panels state (dry run by default).',
    [
      option('apply', '--apply', boolean, {
        default: false,
        description: 'Apply a fresh, immediately re-proven inventory.',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the inventory or result as JSON.',
      }),
    ],
  );
  command(
    'environment inspect',
    'Inspect one environment instance without exposing credential values.',
    [
      option('kind', '--kind <CHOICE>', choice(['live', 'staging']), { required: true }),
      option('instance_id', '--instance-id <TEXT>', text, {}),
      option('environment_root', '--environment-root <PATH>', readable_path('any', false), {
        required: true,
      }),
      option('port', '--port <INTEGER>', integer(), {}),
      option(
        'credentials_env_file',
        '--credentials-env-file <PATH>',
        readable_path('any', false),
        {},
      ),
      option(
        'repository_roots',
        '--repository-root <DIRECTORY>',
        readable_path('directory', true),
        { repeat: true },
      ),
      option('json_output', '--json', boolean, { default: false }),
    ],
  );
  command('environment prepare', 'Materialize one isolated environment instance.', [
    option('kind', '--kind <CHOICE>', choice(['live', 'staging']), { required: true }),
    option('instance_id', '--instance-id <TEXT>', text, {}),
    option('environment_root', '--environment-root <PATH>', readable_path('any', false), {
      required: true,
    }),
    option('port', '--port <INTEGER>', integer(), {}),
    option(
      'credentials_env_file',
      '--credentials-env-file <PATH>',
      readable_path('any', false),
      {},
    ),
    option('repository_roots', '--repository-root <DIRECTORY>', readable_path('directory', true), {
      repeat: true,
    }),
    option('json_output', '--json', boolean, { default: false }),
  ]);
  command(
    'environment provision-skills',
    'Reconcile Panels skills into each production agent home.',
    [
      option('database_parent', '--database-parent <DIRECTORY>', readable_path('directory', true), {
        required: true,
      }),
      option('hermes_home', '--hermes-home <DIRECTORY>', readable_path('directory', false), {
        required: true,
      }),
      option('codex_home', '--codex-home <DIRECTORY>', readable_path('directory', false), {
        required: true,
      }),
      option('claude_home', '--claude-home <DIRECTORY>', readable_path('directory', false), {
        required: true,
      }),
    ],
  );
  command('environment remove', 'Remove the stopped staging environment.', [
    option('kind', '--kind <CHOICE>', choice(['staging']), { required: true }),
    option('instance_id', '--instance-id <TEXT>', text, {}),
    option('environment_root', '--environment-root <PATH>', readable_path('any', false), {
      required: true,
    }),
    option('port', '--port <INTEGER>', integer(), {}),
    option(
      'credentials_env_file',
      '--credentials-env-file <PATH>',
      readable_path('any', false),
      {},
    ),
    option('repository_roots', '--repository-root <DIRECTORY>', readable_path('directory', true), {
      repeat: true,
    }),
    option('json_output', '--json', boolean, { default: false }),
  ]);
  command(
    'environment reset',
    'Rebuild staging fake state while preserving its prepared identity.',
    [
      option('kind', '--kind <CHOICE>', choice(['staging']), { required: true }),
      option('instance_id', '--instance-id <TEXT>', text, {}),
      option('environment_root', '--environment-root <PATH>', readable_path('any', false), {
        required: true,
      }),
      option('port', '--port <INTEGER>', integer(), {}),
      option(
        'credentials_env_file',
        '--credentials-env-file <PATH>',
        readable_path('any', false),
        {},
      ),
      option(
        'repository_roots',
        '--repository-root <DIRECTORY>',
        readable_path('directory', true),
        { repeat: true },
      ),
      option('json_output', '--json', boolean, { default: false }),
    ],
  );
  command(
    'environment restore',
    "Restore a verified snapshot's database and file tree into a stopped live environment.",
    [
      option('snapshot', '--snapshot <DIRECTORY>', readable_path('directory', true), {
        required: true,
      }),
      option('destination_db', '--destination-db <FILE>', readable_path('file', false), {
        required: true,
      }),
      option('live_stopped', '--live-stopped', boolean, { required: true }),
    ],
  );
  command(
    'environment run',
    'Replace this process with `python -m planner serve` for one prepared instance.',
    [
      option('kind', '--kind <CHOICE>', choice(['staging']), { required: true }),
      option('instance_id', '--instance-id <TEXT>', text, {}),
      option('environment_root', '--environment-root <PATH>', readable_path('any', false), {
        required: true,
      }),
      option('port', '--port <INTEGER>', integer(), {}),
      option(
        'credentials_env_file',
        '--credentials-env-file <PATH>',
        readable_path('any', false),
        {},
      ),
      option(
        'repository_roots',
        '--repository-root <DIRECTORY>',
        readable_path('directory', true),
        { repeat: true },
      ),
      option('test_mode', '--test-mode', boolean, { default: false, hidden: true }),
    ],
  );
  command(
    'environment status',
    'Collect a local operational snapshot without contacting the HTTP server.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the sanitized status snapshot as JSON.',
      }),
    ],
  );
  command('executor', 'Run the native Mac executor through the app HTTPS service.');
  command(
    'executor health',
    "Print this executor's identity, process, last accepted lease and any refusal.",
  );
  command(
    'executor permissions',
    'Show narrow access needs. Only --check touches the selected directories.',
    [
      option('workspace', '--workspace <TEXT>', text, { required: true }),
      option('output', '--output <TEXT>', text, { required: true }),
      option('check', '--check', boolean, {
        default: false,
        description: 'Authorize the stated directory read and temporary output check.',
      }),
    ],
  );
  command('executor quiesce', '', [
    option('exit_when_ready', '--exit-when-ready', boolean, { default: false }),
  ]);
  command('executor retry', 'Inspect the original submission. Never repeat or replace its write.', [
    argument('submission_id', '<SUBMISSION_ID>', text, {}),
    option('as_json', '--json', boolean, { default: false }),
  ]);
  command('executor run', '', [
    option('executor_id', '--executor-id <TEXT>', text, { required: true }),
    option('application_revision', '--application-revision <TEXT>', text, { required: true }),
    option('panels_server_url', '--panels-server-url <TEXT>', text, { required: true }),
  ]);
  command(
    'goal',
    'Read, edit, and permanently delete goals.\n\nSee goal schema create|edit|primary-chart for request contracts.',
  );
  command('goal artifact', 'Read and write supporting files, with paths relative to artifacts/.');
  command('goal artifact delete', '', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('goal artifact list', 'List openable paths. Index folders hide their supporting files.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('goal artifact read', '', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('goal artifact write', '', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    option('body_file', '--body-file <TEXT>', text, { required: true }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'goal create',
    'Read JSON from stdin with an explicit display. See goal schema create --json.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'goal delete',
    'Delete a goal permanently. The owner, Chief, and holding Manager can delete it.',
    [
      argument('goal_id', '<GOAL_ID>', text, {}),
      option('yes', '--yes', boolean, {
        default: false,
        description: 'Permanently delete the goal, page, and artifacts.',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'goal edit',
    'Read changes from stdin. Discover display switches with goal schema edit.',
    [
      argument('goal_id', '<GOAL_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('goal list', '', [
    option('active', '--active <BOOLEAN>', boolean, {}),
    option('project_id', '--project-id <TEXT>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'goal make-manager',
    'Move one Chief goal to a new Manager. Read the request body from stdin.',
    [
      argument('goal_id', '<GOAL_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('goal read', '', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    option('first_date', '--first-date <TEXT>', text, {}),
    option('last_date', '--last-date <TEXT>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'goal schema',
    'Show request schema, named examples and notes; no stdin or server needed.',
    [
      argument('operation', '<OPERATION>', choice(['create', 'edit', 'primary-chart']), {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('goal show', '', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'manager',
    'Manage ordinary Managers. Settled Managers are finished destinations.\n\nChoose an active Manager for new work. Reads expose the boolean settled key.\nAn absent settled field does not establish that a Manager is active.\n\nRead manager workspace ID for the visible page and its edit command.\nUse panels page show|edit --manager ID for its own page document.',
  );
  command('manager add-ticket', 'Classify one Ticket under one Manager.', [
    argument('item_id', '<ITEM_ID>', text, {}),
    argument('ticket_id', '<TICKET_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('manager artifact', "Read and write a Manager's own artifacts.");
  command('manager artifact delete', 'Delete one Manager artifact.', [
    argument('item_id', '<ITEM_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'manager artifact list',
    'List openable artifact paths from the workspace. Index folders hide supporting files.',
    [
      argument('item_id', '<ITEM_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('manager artifact read', 'Read one Manager artifact as exact text.', [
    argument('item_id', '<ITEM_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('manager artifact write', 'Write one Manager artifact.', [
    argument('item_id', '<ITEM_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    option('body_file', '--body-file <TEXT>', text, { required: true }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'manager conversation',
    'Compatibility and recovery paths for a Manager conversation.',
    [],
    true,
  );
  command('manager conversation reset', '', [
    argument('item_id', '<ITEM_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('manager conversation send', '', [
    argument('item_id', '<ITEM_ID>', text, {}),
    option('message', '--message <TEXT>', text, { description: 'Message text.' }),
    option('body_file', '--body-file <TEXT>', text, {
      description: 'Read message text from this file, or -.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('manager create', 'Create one Manager.', [
    option('title', '--title <TEXT>', text, { description: 'Manager title.', required: true }),
    option('project', '--project <TEXT>', text, { description: 'Project name.' }),
    option('project_id', '--project-id <TEXT>', text, { description: 'Project id.' }),
    option('goal_file', '--goal-file <TEXT>', text, {
      description: 'Optional goal file, or - for stdin.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('manager delete', 'Permanently delete one empty Manager.', [
    argument('item_id', '<ITEM_ID>', text, {}),
    option('yes', '--yes', boolean, {
      default: false,
      description: 'Permanently delete the manager and its history.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'manager list',
    'List Managers as bounded summaries with the settled boolean. Without --active or --settled, include both states. Settled Managers are finished destinations. Choose an active Manager for new work. An absent settled field does not establish active state.',
    [
      option('search', '--search <TEXT>', text, { description: 'Find a Manager by title.' }),
      option('active', '--active', boolean, {
        default: false,
        description: 'Only show active Managers (settled=false).',
      }),
      option('settled', '--settled', boolean, {
        default: false,
        description: 'Only show settled Managers (finished destinations).',
      }),
      option('project', '--project <TEXT>', text, { description: 'Only show project name.' }),
      option('project_id', '--project-id <TEXT>', text, { description: 'Only show project id.' }),
      option('limit', '--limit <INTEGER>', integer(1), { default: 30 }),
      option('offset', '--offset <INTEGER>', integer(0), { default: 0 }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('manager remove-ticket', "Remove one Ticket's Manager classification.", [
    argument('item_id', '<ITEM_ID>', text, {}),
    argument('ticket_id', '<TICKET_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('manager request-recap', 'Ask a Manager below you for a fresh recap.', [
    argument('item_id', '<ITEM_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'manager set',
    'Change one plain Manager field. The goal field is its purpose, not its page. Use panels manager workspace ID for the visible page and its edit command. Use panels page edit --manager ID for its own page document.',
    [
      argument('item_id', '<ITEM_ID>', text, {}),
      argument('field', '<FIELD>', choice(['goal', 'project', 'project-id', 'recap', 'title']), {}),
      option('value', '--value <TEXT>', text, { description: 'Set the field to this value.' }),
      option('body_file', '--body-file <TEXT>', text, {
        description: 'Read field text from this file, or - for stdin.',
      }),
      option('clear', '--clear', boolean, {
        default: false,
        description: 'Clear nullable fields.',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('manager settle', 'Set the owner-controlled settled state of an ordinary manager.', [
    argument('item_id', '<ITEM_ID>', text, {}),
    option('active', '--active', boolean, {
      default: false,
      description: 'Return to active instead.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'manager show',
    'Read one Manager record or selected parts, including the settled boolean. Settled Managers are finished destinations. Choose an active Manager for new work. An absent settled field does not establish active state. Parts include goal, recap, and page. Read manager workspace ID to identify the visible document before a page edit.',
    [
      argument('item_id', '<ITEM_ID>', text, {}),
      argument('part_names', '[PART_NAMES]', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command(
    'manager workspace',
    "Read one Manager's purpose, recap, held goals, artifacts, and child Tickets. Identify the visible page and its exact read and edit commands. Exactly one held goal supplies the page instead of the Manager document.",
    [
      argument('item_id', '<ITEM_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command(
    'measure',
    'Define measures and read their dated values. See measure schema for create input.',
  );
  command(
    'measure create',
    'Read JSON from stdin. See measure schema --json for the complete create contract.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('measure delete', 'Delete a definition when canonical dependency checks permit it.', [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('measure edit', 'Read changes and expected_revision as JSON from stdin.', [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('measure list', 'List a bounded page of definitions.', [
    option('project_id', '--project-id <TEXT>', text, {}),
    option('source_id', '--source-id <TEXT>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('measure manual-state', "Read a manual measure's current or selected date state.", [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    option('selected_date', '--date <TEXT>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'measure manual-write',
    'Read a manual replace or increment request as JSON from stdin.',
    [
      argument('measure_id', '<MEASURE_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('measure read', 'Read {measure_ids, first_date, last_date, limit?} JSON from stdin.', [
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'measure schema',
    'Show create request schema, named examples and semantic notes; no server needed.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('measure show', 'Show one definition.', [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'measure-source',
    'Configure sources and runs. Discover contracts with schema and runtime --json.',
  );
  command(
    'measure-source create',
    'Read JSON from stdin. Discover fields with measure-source schema create --json.\n\nAfter creating a source, read measure-source runtime --json before writing its script.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('measure-source delete', 'Delete a source when canonical dependency checks permit it.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('measure-source list', 'List a bounded page of sources and their collection health.', [
    option('project_id', '--project-id <TEXT>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'measure-source request-run',
    'Enqueue JSON from stdin; see measure-source schema run --json for fields.\n\nRead measure-source runtime --json for candidates, collection and script behavior.',
    [
      argument('source_id', '<SOURCE_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'measure-source run-show',
    'Show an explicit run ID, safe failure details, and test candidates.',
    [
      argument('run_id', '<RUN_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('measure-source runs', 'List a bounded page of runs for one source.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'measure-source runtime',
    'Show script helpers, context and an executable example; no server or stdin needed.\n\nRequest envelopes are in measure-source schema script|run --json.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'measure-source schema',
    'Show request contracts offline. See runtime --json for script execution helpers.',
    [
      argument('operation', '[OPERATION]', choice(['create', 'script', 'run']), {
        default: 'create',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'measure-source set-responsible',
    'Read {expected_responsibility_revision, responsible} JSON from stdin.',
    [
      argument('source_id', '<SOURCE_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'measure-source set-schedule',
    'Read {expected_revision, enabled, local_time} JSON from stdin.',
    [
      argument('source_id', '<SOURCE_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('measure-source show', 'Show configuration and script revisions.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('measure-source status', 'Show collection health, its failure, and current activity.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command(
    'measure-source submit-script',
    'Save JSON from stdin; see measure-source schema script --json for fields.\n\nRead measure-source runtime --json for Python helpers and dated outputs first.',
    [
      argument('source_id', '<SOURCE_ID>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'page',
    "Read and edit the page documents that Managers and Chief can change.\n\nSelect --manager ID or --chief. For a sole held goal's visible document,\nuse goal show|edit. Start with manager workspace ID to find the visible source.",
  );
  command(
    'page edit',
    'Read {page: DOCUMENT} from stdin. See page schema before a whole-document edit.',
    [
      option('manager_id', '--manager <TEXT>', text, {
        description: "Edit this Manager's own page document.",
      }),
      option('chief', '--chief', boolean, {
        default: false,
        description: 'Edit the Chief page document.',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command(
    'page schema',
    'Show the page edit envelope, shared document schema, examples, and notes.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print the canonical JSON response.',
      }),
    ],
  );
  command('page show', 'Read the saved holder document and its page_revision.', [
    option('manager_id', '--manager <TEXT>', text, {
      description: "Read this Manager's own page document.",
    }),
    option('chief', '--chief', boolean, {
      default: false,
      description: 'Read the Chief page document.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print the canonical JSON response.',
    }),
  ]);
  command('preview', "Prepare, publish, inspect, or stop your Ticket's application preview.");
  command('preview prepare', 'Reserve a generation prefix before starting your application.', [
    option('as_json', '--json', boolean, { default: false }),
  ]);
  command('preview register', 'Verify a loopback listener and return its application link.', [
    option('port', '--port <INTEGER>', integer(1024, 65535), { required: true }),
    option('as_json', '--json', boolean, { default: false }),
  ]);
  command('preview show', 'Show the current generation and link.', [
    option('as_json', '--json', boolean, { default: false }),
  ]);
  command('preview stop', 'End this generation and close its active requests.', [
    option('as_json', '--json', boolean, { default: false }),
  ]);
  command('project', 'List, inspect, create, and update projects.');
  command('project create', 'Create one Project.', [
    option('name', '--name <TEXT>', text, { description: 'Project display name.', required: true }),
    option('summary', '--summary <TEXT>', text, { description: 'Optional project summary text.' }),
    option('folder_path', '--folder-path <TEXT>', text, {
      description: 'Optional absolute Project folder path.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('project list', 'List Projects as bounded summaries.', [
    option('limit', '--limit <INTEGER>', integer(1), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(0), { default: 0 }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('project set', 'Change one Project field.', [
    argument('project_id', '<PROJECT_ID>', text, {}),
    argument('field', '<FIELD>', choice(['folder-path', 'name', 'summary']), {}),
    option('value', '--value <TEXT>', text, { description: 'Set the field to this value.' }),
    option('body_file', '--body-file <TEXT>', text, {
      description: 'Read field text from this file, or - for stdin.',
    }),
    option('clear', '--clear', boolean, {
      default: false,
      description: 'Set the field to empty text.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('project show', 'Read one Project record or selected parts.', [
    argument('project_id', '<PROJECT_ID>', text, {}),
    argument('part_names', '[PART_NAMES]', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'restart',
    'Ask the foreground Panels supervisor to replace its application child.',
    [],
    true,
  );
  command(
    'send-message',
    'Send one message to one existing Panels conversation.\n\nMessages support Markdown tables and inline charts. Put chart JSON in a closed panels-chart fence. Use --body-file to preserve the fence and newlines. Charts use inline snapshot numbers, not live measure or dataset bindings.\n\n\b\n```panels-chart\n{"kind":"bar","series":[{"name":"PRs","points":[{"label":"Week 1","value":4}]}]}\n```\n\nUse kind bar or line. Add more named series for comparisons. Optional fields: title, unit, format (prefix, suffix, decimals), and stacked for bars. Values accept fractions and range from -9007199254740991 to 9007199254740991. Decimals must be an integer from 0 to 20. Point labels must be unique within each series. A null point value marks missing data. Invalid, unsupported, or incomplete specs stay visible as code.',
    [
      option('owner', '--owner', boolean, { default: false, description: 'Send to Khushal.' }),
      option('chief', '--chief', boolean, {
        default: false,
        description: 'Send to the Chief of Staff.',
      }),
      option('ticket_id', '--ticket <TEXT>', text, { description: 'Send to a Ticket worker.' }),
      option('manager_id', '--manager <TEXT>', text, { description: 'Send to a Manager.' }),
      option('mode', '--mode <CHOICE>', choice(['queue', 'steer', 'send_now']), {
        default: 'steer',
        description: 'Steer into the running turn, queue behind it, or interrupt it and send now.',
      }),
      option('message', '--message <TEXT>', text, {
        description: 'Message text. Addressed agent turns must reply through Send Message.',
      }),
      option('body_file', '--body-file <TEXT>', text, {
        description: 'Read message text from this file, or -.',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('serve', 'Start the web server and background planner runtime.', [], true);
  command(
    'server-event-loop-tasks',
    'List what the running server waits on: every asyncio task on its event loop.\n\nEach task shows its name, coroutine, the line it is awaiting, and its age, oldest\nfirst. Reads only: no task is cancelled, woken, or changed.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
    true,
  );
  command('skill', 'Inspect and edit the managed skills.', [], true);
  command('skill apply', 'Atomically apply a reviewed skill manifest.', [
    option('input_json', '--input-json <TEXT>', text, {
      default: '-',
      description: 'Exact manifest file, or - for stdin.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'skill check',
    'Report every skill row naming a command, Stage or skill this build lacks.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command(
    'skill delete',
    'Delete a skill and its whole folder. Refuses a skill a Worker type uses.',
    [
      argument('name', '<NAME>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('skill edit', 'Edit a skill. Optional Markdown body on redirected stdin.', [
    argument('name', '<NAME>', text, {}),
    option('description', '--description <TEXT>', text, {
      description: 'The one-line description in the frontmatter.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('skill list', 'List the managed skill names.', [
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('skill show', "Print a skill's Markdown body, or its full record as JSON.", [
    argument('name', '<NAME>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'skill write-file',
    "Write a file at PATH inside a skill's folder. File text on redirected stdin.\n\nThe skill's own SKILL.md is not writable here; use `panels skill edit`.",
    [
      argument('name', '<NAME>', text, {}),
      argument('path', '<PATH>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('stalls', 'Alarms on stalled Panels work.', [], true);
  command(
    'stalls watchdog',
    'Alarm once on each open stall finding and on a stale checker heartbeat.\n\nReads the database read-only. Push settings come from the live configuration\n(PLAN_CONFIG_PATH and the PLAN_APNS_* environment); the Chief message goes to the\nlocal server on the configured port.',
    [
      option('db_path', '--db <FILE>', readable_path('file', true), { required: true }),
      option('state_dir', '--state-dir <DIRECTORY>', readable_path('directory', false), {
        required: true,
      }),
    ],
  );
  command('ticket', 'Create, inspect, organize, and approve tickets.');
  command(
    'ticket assign',
    'Assign one Worker to an explicit Manager.\n\nChoose an active Manager for new work. Settled Managers are finished\ndestinations. Read the settled boolean first. An absent settled field does not\nestablish active state.',
    [
      argument('ticket_id', '<TICKET_ID>', text, {}),
      option('chief', '--chief', boolean, { default: false, description: 'Assign to Chief.' }),
      option('item_id', '--manager <TEXT>', text, { description: 'Ordinary manager id.' }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('ticket backlog', "Defer automatic worker steps while preserving the Ticket's work.", [
    argument('ticket_id', '[TICKET_ID]', text, { envNames: ['PLAN_TICKET_ID'] }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket block', 'Add one blocking Ticket relationship.', [
    argument('ticket_id', '<TICKET_ID>', text, {}),
    option('blocker_id', '--by <TEXT>', text, {
      description: 'Blocking ticket id.',
      required: true,
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket complete', 'Complete one user-owned Stage directly.', [
    argument('ticket_id', '<TICKET_ID>', text, {}),
    argument('field', '<FIELD>', text, {}),
    option('value', '--value <TEXT>', text, { description: 'Complete the gate with this value.' }),
    option('body_file', '--body-file <TEXT>', text, {
      description: 'Read the value from this file, or - for stdin.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'ticket create',
    'Create one Ticket from one JSON object. Choose an active Manager for new work. Settled Managers are finished destinations. Read the settled boolean first. An absent settled field does not establish active state.',
    [
      option('input_json', '--input-json <TEXT>', text, {
        description: 'Read one Ticket create object from this file, or - for standard input.',
        required: true,
      }),
      option('locale', '--locale <CHOICE>', choice(['vps', 'macos']), {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('ticket delete', 'Permanently delete one Ticket after explicit confirmation.', [
    argument('ticket_id', '[TICKET_ID]', text, { envNames: ['PLAN_TICKET_ID'] }),
    option('force', '--force', boolean, {
      default: false,
      description: 'Delete even a ticket that still looks running, for a stuck ticket status.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket edit', 'Apply one or more Ticket field edits from one JSON object.', [
    argument('ticket_id', '[TICKET_ID]', text, {}),
    option('input_json', '--input-json <TEXT>', text, {
      description: 'Read one Ticket edit object from this file, or - for standard input.',
      required: true,
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'ticket employee-configuration',
    "Set what this Ticket's worker launches on: backend, model, reasoning effort.\n\nAll three go together because the door takes them together — a model id belongs to the\nbackend that named it, so there is no such thing as changing one of them on its own.",
    [
      argument('ticket_id', '<TICKET_ID>', text, {}),
      option('backend', '--backend <TEXT>', text, {
        description: 'Registered employee backend.',
        required: true,
      }),
      option('model', '--model <TEXT>', text, {
        description: "Model that backend runs this Ticket's worker on.",
        required: true,
      }),
      option('reasoning_effort', '--reasoning-effort <TEXT>', text, {
        description: 'Reasoning effort for that model; leave it out for a model that takes none.',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
    true,
  );
  command('ticket history', 'Read the current Worker conversation for one Ticket.', [
    argument('ticket_id', '[TICKET_ID]', text, { envNames: ['PLAN_TICKET_ID'] }),
    option('limit', '--limit <INTEGER>', integer(1, 100), { default: 30 }),
    option('before', '--before <INTEGER>', integer(), {
      description: 'Read the page before this sequence.',
    }),
    option('after', '--after <INTEGER>', integer(), {
      description: 'Read forwards from this sequence instead of back from the end.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket list', 'List Tickets with bounded filters.', [
    option('is_backlogged', '--backlogged', boolean, {
      description: 'Filter explicit Ticket deferral.',
    }),
    option('is_backlogged', '--not-backlogged', boolean, {
      inverse: true,
      description: 'Filter explicit Ticket deferral.',
    }),
    option('worker_type', '--worker-type <TEXT>', text, {
      description: 'Filter Worker type. Repeat for more.',
      repeat: true,
    }),
    option('stage', '--stage <TEXT>', text, {
      description: 'Include this Stage. Repeat for more.',
      repeat: true,
    }),
    option('exclude_stage', '--exclude-stage <TEXT>', text, {
      description: 'Exclude this Stage. Repeat for more.',
      repeat: true,
    }),
    option('ticket_status', '--ticket-status <TEXT>', text, {
      description: 'Include this ticket status. Repeat for more.',
      repeat: true,
    }),
    option('exclude_ticket_status', '--exclude-ticket-status <TEXT>', text, {
      description: 'Exclude this ticket status. Repeat for more.',
      repeat: true,
    }),
    option('include_terminal', '--include-terminal', boolean, {
      default: false,
      description: 'Include done Tickets.',
    }),
    option('search', '--search <TEXT>', text, {
      description: 'Case-insensitive Ticket text search.',
    }),
    option('project', '--project <TEXT>', text, { description: 'Only show project name.' }),
    option('project_id', '--project-id <TEXT>', text, { description: 'Only show project id.' }),
    option('manager', '--manager <TEXT>', text, { description: 'Only show tickets in this item.' }),
    option('limit', '--limit <INTEGER>', integer(1), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(0), { default: 0 }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket proposal', 'Submit, accept, or revise the current Ticket proposal.', [
    argument('proposal_args', '[PROPOSAL_ARGS...]', text, {}),
    option('ceiling', '--ceiling <TEXT>', text, {
      description: 'Next ceiling for an accepted proposal.',
    }),
    option('holder', '--holder <TEXT>', text, {
      description: 'Next ceiling holder for an accepted proposal.',
    }),
    option('edit_file', '--edit-file <TEXT>', text, {
      description: 'Edited accepted body, or - for stdin.',
    }),
    option('kickoff_title', '--kickoff-title <TEXT>', text, {
      description: 'Edited Kickoff title.',
    }),
    option('kickoff_note_file', '--kickoff-note-file <TEXT>', text, {
      description: 'Edited Kickoff note, or - for stdin.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket request-help', 'Record and send one Ticket help request from standard input.', [
    argument('ticket_id', '[TICKET_ID]', text, {}),
    option('owner', '--owner', boolean, { default: false, description: 'Ask Khushal for help.' }),
    option('chief', '--chief', boolean, {
      default: false,
      description: 'Ask the Chief of Staff for help.',
    }),
    option('recipient_ticket_id', '--ticket <TEXT>', text, { description: 'Ask another Worker.' }),
    option('recipient_manager_id', '--manager <TEXT>', text, { description: 'Ask a Manager.' }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket request-recap', 'Ask a Ticket below you for a fresh recap.', [
    argument('ticket_id', '<TICKET_ID>', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket restart-worker', 'Restart one dead Worker through the existing recovery route.', [
    argument('ticket_id', '<TICKET_ID>', text, {}),
    option('backend', '--backend <TEXT>', text, {
      description: 'Registered employee backend to restart on.',
    }),
    option('model', '--model <TEXT>', text, {
      description: "Model that backend runs this Ticket's worker on.",
    }),
    option('reasoning_effort', '--reasoning-effort <TEXT>', text, {
      description: 'Reasoning effort for that model; leave it out for a model that takes none.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket resume', "Resume automatic steps under the Ticket's existing gates.", [
    argument('ticket_id', '[TICKET_ID]', text, { envNames: ['PLAN_TICKET_ID'] }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket show', "Read the current Worker's Ticket or one named Ticket.", [
    argument('ticket_id', '[TICKET_ID]', text, {}),
    argument('part_names', '[PART_NAMES]', text, {}),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('ticket unblock', 'Remove one blocking Ticket relationship.', [
    argument('ticket_id', '<TICKET_ID>', text, {}),
    option('blocker_id', '--by <TEXT>', text, {
      description: 'Blocking ticket id.',
      required: true,
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('worker', 'Worker-only ticket and item writes.', [], true);
  command(
    'worker my-ticket',
    "Show the worker's Ticket manifest or selected comma-separated PART_NAMES.",
    [
      argument('part_names', '[PART_NAMES]', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('worker propose', '', [
    argument('ticket_id', '[TICKET_ID]', text, { envNames: ['PLAN_TICKET_ID'] }),
    option('body_file', '--body-file <TEXT>', text, {
      description: 'Removed: pipe proposal text on stdin instead.',
    }),
    option('recap', '--recap <TEXT>', text, {
      description: 'Removed: use `worker recap` instead.',
    }),
    option('recap_file', '--recap-file <TEXT>', text, {
      description: 'Removed: use `worker recap` instead.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('worker recap', '', [
    argument('ticket_id', '[TICKET_ID]', text, { envNames: ['PLAN_TICKET_ID'] }),
    option('body_file', '--body-file <TEXT>', text, {
      description: 'Removed: pipe recap text on stdin instead.',
    }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('worker request-help', '', [
    argument('ticket_id', '[TICKET_ID]', text, { envNames: ['PLAN_TICKET_ID'] }),
    option('owner', '--owner', boolean, { default: false, description: 'Ask Khushal for help.' }),
    option('chief', '--chief', boolean, {
      default: false,
      description: 'Ask the Chief of Staff for help.',
    }),
    option('recipient_ticket_id', '--ticket <TEXT>', text, {
      description: 'Ask another Ticket worker.',
    }),
    option('recipient_manager_id', '--manager <TEXT>', text, { description: 'Ask a Manager.' }),
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command('worker-type', 'Inspect and declare the Worker types.', [], true);
  command(
    'worker-type delete',
    'Remove a Worker type. Its Tickets keep their own workflow and skill.',
    [
      argument('worker_type', '<WORKER_TYPE>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command('worker-type list', '', [
    option('as_json', '--json', boolean, {
      default: false,
      description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
    }),
  ]);
  command(
    'worker-type save',
    'Declare a Worker type, or replace the one with this id. Record on stdin.',
    [
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command(
    'worker-type show',
    "Print one Worker type's stored record, in the shape `save` takes back.",
    [
      argument('worker_type', '<WORKER_TYPE>', text, {}),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  command(
    'worker-type skill',
    "Edit this Worker type's skill. Optional Markdown body on redirected stdin.",
    [
      argument('worker_type', '<WORKER_TYPE>', text, {}),
      option('description', '--description <TEXT>', text, {
        description: 'The one-line description in the frontmatter.',
      }),
      option('as_json', '--json', boolean, {
        default: false,
        description: 'Print machine-readable JSON. Errors are printed as JSON on stderr.',
      }),
    ],
  );
  return root;
}
