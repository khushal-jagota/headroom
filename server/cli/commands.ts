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
  readable_file,
  type Audience,
} from './declarations.ts';
import { PROVISION_SKILLS_HOME_OPTIONS } from '../environments/command_contracts.ts';
import { TICKET_NEEDS } from '../tickets/contracts.ts';

/** `--json` means one thing on every command. */
const json_output = (key = 'as_json') =>
  option(key, '--json', boolean, { default: false, description: 'Print the result as JSON.' });
/** The JSON input rule: `--input-json FILE`, `-` for stdin, or stdin when the flag is absent. */
const json_input = () =>
  option('input_json', '--input-json <FILE>', readable_file, {
    description: 'Read the JSON object from this file, or - for stdin. Default: stdin.',
  });
/** The text input rule: the inline flag, `--body-file FILE`, or stdin when neither is given. */
const text_input = (inline: 'message' | 'value' = 'message') => [
  inline === 'message'
    ? option('message', '--message <TEXT>', text, { description: 'Use this text.' })
    : option('value', '--value <TEXT>', text, { description: 'Set the field to this text.' }),
  option('body_file', '--body-file <FILE>', readable_file, {
    description: 'Read the text from this file, or - for stdin. Default: stdin.',
  }),
];
const page_inputs = () => [
  option('limit', '--limit <INTEGER>', integer(1), { default: 30 }),
  option('offset', '--offset <INTEGER>', integer(0), { default: 0 }),
];
const MANAGER_ID = () => argument('manager_id', '<MANAGER_ID>', text);
const TICKET_ID = () => argument('ticket_id', '<TICKET_ID>', text);

export function create_commands(environment: NodeJS.ProcessEnv = {}): CliCommand {
  const root = new CliCommand(
    [],
    'Read and change Panels work: Tickets, Managers, pages, and data.',
  );
  const by_path = new Map<string, CliCommand>([['', root]]);
  const group_audiences = new Map<string, Audience>();
  /** A group: its leaves take the group's audience unless they state their own. */
  const group = (path: string, description: string, audience: Audience | null = null) => {
    const words = path.split(' ');
    const current = new CliCommand(words, description);
    by_path.get(words.slice(0, -1).join(' '))!.addCommand(current);
    by_path.set(path, current);
    if (audience) group_audiences.set(path, audience);
  };
  const command = (
    path: string,
    description: string,
    inputs: Parameters<CliCommand['addInputs']>[0] = [],
    settings: { audience?: Audience; hidden?: boolean } = {},
  ) => {
    const words = path.split(' ');
    const current = new CliCommand(words, description).addInputs(inputs, environment);
    const audience = settings.audience ?? group_audiences.get(words.slice(0, -1).join(' '));
    if (audience === undefined) throw new Error(`command has no audience: ${path}`);
    current.audience = audience;
    by_path.get(words.slice(0, -1).join(' '))!.addCommand(current, { hidden: settings.hidden });
    by_path.set(path, current);
  };

  command(
    'api',
    "Call a Panels API path with this conversation's credential.",
    [
      argument('method', '<METHOD>', text),
      argument('path', '<PATH>', text),
      option('input_json', '--input-json <FILE>', readable_file, {
        description: 'Send the JSON body in this file, or - for stdin.',
      }),
      json_output(),
    ],
    { audience: 'everyone', hidden: true },
  );

  group('attention', 'Read where owner involvement went over selected dates.', 'data');
  command('attention setup-collection', 'Set up daily collection of attention figures.', [
    option('project_id', '--project <TEXT>', text, { required: true }),
    option('database_path', '--database-path <PATH>', readable_path('any', false), {
      description: 'Absolute database path on the Panels server, read-only during collection.',
      required: true,
    }),
    option('local_time', '--local-time <TEXT>', text, { default: '00:05' }),
    json_output(),
  ]);
  command(
    'attention summary',
    'Compare recorded owner involvement with the preceding date range.',
    [
      option('first_date', '--first-date <TEXT>', text, {
        description: 'First inclusive local date (YYYY-MM-DD). Default: seven dates ago.',
      }),
      option('last_date', '--last-date <TEXT>', text, {
        description: 'Last inclusive local date (YYYY-MM-DD). Default: today.',
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
      json_output(),
    ],
  );

  group('credential', 'List and request credentials by name, never by secret value.', 'data');
  command('credential list', 'List credential names and metadata without secret values.', [
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    json_output(),
  ]);
  command('credential rename', 'Rename a credential without exposing its value.', [
    argument('credential_id', '<CREDENTIAL_ID>', text, {}),
    option('name', '--name <TEXT>', text, { required: true }),
    option('expected_revision', '--expected-revision <INTEGER>', integer(1), { required: true }),
    json_output(),
  ]);
  command('credential request', 'Ask Khushal for a named API key in this conversation.', [
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
    json_output(),
  ]);

  group('dataset', 'Define datasets and read their dated rows.', 'data');
  command('dataset create', 'Create a dataset from a JSON definition.', [
    json_input(),
    json_output(),
  ]);
  command('dataset edit', 'Change a dataset from a JSON object with expected_revision.', [
    argument('dataset_id', '<DATASET_ID>', text, {}),
    json_input(),
    json_output(),
  ]);
  command('dataset list', 'List dataset definitions.', [
    option('project_id', '--project-id <TEXT>', text, {}),
    option('source_id', '--source-id <TEXT>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    json_output(),
  ]);
  command(
    'dataset read',
    'Read dated rows from a JSON request {dataset_ids, first_date, last_date, limit?}.',
    [json_input(), json_output()],
  );
  command('dataset show', 'Show one dataset definition.', [
    argument('dataset_id', '<DATASET_ID>', text, {}),
    json_output(),
  ]);

  const environment_inputs = (kinds: string[]) => [
    option('kind', '--kind <CHOICE>', choice(kinds), { required: true }),
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
  ];
  group('environment', 'Prepare and run isolated Panels environments.', 'host');
  command('environment app-build', 'Build and validate one host-native app at an exact SHA.', [
    option('source_root', '--source-root <DIRECTORY>', readable_path('directory', true), {
      required: true,
    }),
    option('requested_sha', '--requested-sha <TEXT>', text, { required: true }),
    option('candidate_app', '--candidate-app <DIRECTORY>', readable_path('directory', false), {
      required: true,
    }),
  ]);
  command('environment app-identity', 'Print the validated SHA of one app directory.', [
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
      json_output(),
    ],
  );
  command(
    'environment inspect',
    'Inspect one environment instance without exposing credential values.',
    [...environment_inputs(['live', 'staging']), json_output('json_output')],
  );
  command('environment prepare', 'Materialize one isolated environment instance.', [
    ...environment_inputs(['live', 'staging']),
    json_output('json_output'),
  ]);
  command(
    'environment provision-skills',
    'Reconcile Panels skills into each production agent home.',
    [
      option('database_parent', '--database-parent <DIRECTORY>', readable_path('directory', true), {
        required: true,
      }),
      ...PROVISION_SKILLS_HOME_OPTIONS.map(({ cli_option, destination }) =>
        option(destination, `${cli_option} <DIRECTORY>`, readable_path('directory', false), {
          required: true,
        }),
      ),
    ],
  );
  command('environment remove', 'Remove the stopped staging environment.', [
    ...environment_inputs(['staging']),
    json_output('json_output'),
  ]);
  command('environment reset', 'Rebuild staging fake state while keeping its prepared identity.', [
    ...environment_inputs(['staging']),
    json_output('json_output'),
  ]);
  command(
    'environment restore',
    "Restore a verified snapshot's database and files into a stopped live environment.",
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
  command('environment run', 'Replace this process with panels serve for one prepared instance.', [
    ...environment_inputs(['staging']),
    option('test_mode', '--test-mode', boolean, { default: false, hidden: true }),
  ]);
  command(
    'environment status',
    'Collect a local operational snapshot without contacting the HTTP server.',
    [json_output()],
  );

  group('executor', 'Run a Panels executor on this machine.', 'host');
  command(
    'executor credential',
    'Install a registration or replacement credential into this executor root.',
    [option('source', '--file <FILE>', readable_path('file', true), { required: true })],
  );
  command(
    'executor health',
    "Print this executor's identity, process, last accepted lease, and any refusal.",
  );
  command(
    'executor permissions',
    'Show the access this executor needs. Only --check touches the directories.',
    [
      option('workspace', '--workspace <TEXT>', text, { required: true }),
      option('output', '--output <TEXT>', text, { required: true }),
      option('check', '--check', boolean, {
        default: false,
        description: 'Authorize the stated directory read and temporary output check.',
      }),
    ],
  );
  command('executor quiesce', 'Ask the running executor to stop taking new work.', [
    option('exit_when_ready', '--exit-when-ready', boolean, { default: false }),
  ]);
  command('executor retry', 'Look up the receipt of one earlier submission.', [
    argument('submission_id', '<SUBMISSION_ID>', text, {}),
    json_output(),
  ]);
  command('executor run', 'Run the executor service for one registered machine.', [
    option('executor_id', '--executor-id <TEXT>', text, { required: true }),
    option('application_revision', '--application-revision <TEXT>', text, { required: true }),
    option('panels_server_url', '--panels-server-url <TEXT>', text, { required: true }),
  ]);

  group('goal', 'Read, edit, and delete goals.', 'data');
  group('goal artifact', 'Read and write the supporting files of a goal.', 'data');
  command('goal artifact delete', 'Delete one goal artifact.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    json_output(),
  ]);
  command('goal artifact list', 'List the openable artifact paths of a goal.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    json_output(),
  ]);
  command('goal artifact read', 'Print one goal artifact as exact text.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    json_output(),
  ]);
  command('goal artifact write', 'Write one goal artifact.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    ...text_input(),
    json_output(),
  ]);
  command('goal create', 'Create a goal from a JSON object.', [json_input(), json_output()]);
  command('goal delete', 'Delete a goal, its page, and its artifacts permanently.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    option('yes', '--yes', boolean, {
      default: false,
      description: 'Confirm the permanent deletion.',
    }),
    json_output(),
  ]);
  command('goal edit', 'Change a goal from a JSON object.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    json_input(),
    json_output(),
  ]);
  command('goal list', 'List goals.', [
    option('active', '--active <BOOLEAN>', boolean, {}),
    option('project_id', '--project-id <TEXT>', text, {}),
    json_output(),
  ]);
  command('goal make-manager', 'Move one Chief goal to a new Manager.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    json_input(),
    json_output(),
  ]);
  command('goal read', "Read a goal's measure readings over a date range.", [
    argument('goal_id', '<GOAL_ID>', text, {}),
    option('first_date', '--first-date <TEXT>', text, {}),
    option('last_date', '--last-date <TEXT>', text, {}),
    json_output(),
  ]);
  command('goal schema', 'Show the goal request schema, examples, and notes.', [
    argument('operation', '<OPERATION>', choice(['create', 'edit', 'primary-chart']), {}),
    json_output(),
  ]);
  command('goal show', 'Show one goal and its page.', [
    argument('goal_id', '<GOAL_ID>', text, {}),
    json_output(),
  ]);

  group('machine', 'Read and register named execution destinations.', 'data');
  command('machine list', 'List destination machines and whether each is available.', [
    json_output(),
  ]);
  command(
    'machine register',
    'Register an extra machine and print its credential once.',
    [argument('name', '<NAME>', text, {}), json_output()],
    { audience: 'host' },
  );
  command(
    'machine join-command',
    'Register a machine and print its private join command.',
    [
      argument('name', '<NAME>', text, {}),
      option('home_url', '--home <TEXT>', text, { required: true }),
    ],
    { audience: 'host' },
  );
  command(
    'machine replace-credential',
    'Revoke the credential of a machine and print its replacement once.',
    [argument('machine_id', '<MACHINE_ID>', text, {}), json_output()],
    { audience: 'host' },
  );

  group('manager', 'Read and change Managers.', 'supervisor');
  group('manager artifact', "Read and write a Manager's own artifacts.", 'supervisor');
  command('manager artifact delete', 'Delete one Manager artifact.', [
    MANAGER_ID(),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    json_output(),
  ]);
  command('manager artifact list', 'List the openable artifact paths of a Manager.', [
    MANAGER_ID(),
    json_output(),
  ]);
  command('manager artifact read', 'Print one Manager artifact as exact text.', [
    MANAGER_ID(),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    json_output(),
  ]);
  command('manager artifact write', 'Write one Manager artifact.', [
    MANAGER_ID(),
    argument('artifact_path', '<ARTIFACT_PATH>', text, {}),
    ...text_input(),
    json_output(),
  ]);
  command('manager create', 'Create one Manager.', [
    option('title', '--title <TEXT>', text, { description: 'Manager title.', required: true }),
    option('project', '--project <TEXT>', text, { description: 'Project name.' }),
    option('project_id', '--project-id <TEXT>', text, { description: 'Project id.' }),
    option('goal_file', '--goal-file <FILE>', readable_file, {
      description: 'Read the Manager purpose from this file, or - for stdin.',
    }),
    json_output(),
  ]);
  command('manager delete', 'Delete one empty Manager permanently.', [
    MANAGER_ID(),
    option('yes', '--yes', boolean, {
      default: false,
      description: 'Confirm the permanent deletion.',
    }),
    json_output(),
  ]);
  command(
    'manager list',
    'List active Managers and the needs of their Tickets.',
    [
      option('search', '--search <TEXT>', text, { description: 'Find a Manager by title.' }),
      option('settled', '--settled', boolean, {
        default: false,
        description: 'Show settled Managers only.',
      }),
      option('all', '--all', boolean, {
        default: false,
        description: 'Show active and settled Managers.',
      }),
      option('project', '--project <TEXT>', text, { description: 'Only this project name.' }),
      option('project_id', '--project-id <TEXT>', text, { description: 'Only this project id.' }),
      ...page_inputs(),
      json_output(),
    ],
    { audience: 'everyone' },
  );
  command('manager request-recap', 'Ask a Manager below you for a fresh recap.', [
    MANAGER_ID(),
    json_output(),
  ]);
  command('manager set', 'Change one Manager field. The goal field is its purpose.', [
    MANAGER_ID(),
    argument('field', '<FIELD>', choice(['goal', 'project', 'project-id', 'recap', 'title']), {}),
    ...text_input('value'),
    option('clear', '--clear', boolean, { default: false, description: 'Clear a nullable field.' }),
    json_output(),
  ]);
  command('manager settle', 'Mark a Manager settled, or active again with --active.', [
    MANAGER_ID(),
    option('active', '--active', boolean, {
      default: false,
      description: 'Return the Manager to active.',
    }),
    json_output(),
  ]);
  command(
    'manager show',
    'Read one Manager, or your own, with its visible page and Ticket needs.',
    [
      argument('manager_id', '[MANAGER_ID]', text, {}),
      argument('part_names', '[PART_NAMES...]', text, {
        description: 'Parts to print: goal, recap. Separate them by spaces or commas.',
      }),
      json_output(),
    ],
    { audience: 'everyone' },
  );

  group('measure', 'Define measures and read their dated values.', 'data');
  command('measure create', 'Create a measure from a JSON definition.', [
    json_input(),
    json_output(),
  ]);
  command('measure delete', 'Delete a measure that nothing depends on.', [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    json_output(),
  ]);
  command('measure edit', 'Change a measure from a JSON object with expected_revision.', [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    json_input(),
    json_output(),
  ]);
  command('measure list', 'List measure definitions.', [
    option('project_id', '--project-id <TEXT>', text, {}),
    option('source_id', '--source-id <TEXT>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    json_output(),
  ]);
  command('measure manual-state', "Read a manual measure's state for today or one date.", [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    option('selected_date', '--date <TEXT>', text, {}),
    json_output(),
  ]);
  command('measure manual-write', 'Replace or increment a manual value from a JSON request.', [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    json_input(),
    json_output(),
  ]);
  command(
    'measure read',
    'Read dated values from a JSON request {measure_ids, first_date, last_date, limit?}.',
    [json_input(), json_output()],
  );
  command('measure schema', 'Show the measure create schema, examples, and notes.', [
    json_output(),
  ]);
  command('measure show', 'Show one measure definition.', [
    argument('measure_id', '<MEASURE_ID>', text, {}),
    json_output(),
  ]);

  group('measure-source', 'Configure measure sources and their runs.', 'data');
  command('measure-source create', 'Create a measure source from a JSON definition.', [
    json_input(),
    json_output(),
  ]);
  command('measure-source delete', 'Delete a measure source that nothing depends on.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    json_output(),
  ]);
  command('measure-source list', 'List measure sources and their collection health.', [
    option('project_id', '--project-id <TEXT>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    json_output(),
  ]);
  command('measure-source request-run', 'Queue a run of a measure source from a JSON request.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    json_input(),
    json_output(),
  ]);
  command('measure-source run-show', 'Show one run, its failure details, and test candidates.', [
    argument('run_id', '<RUN_ID>', text, {}),
    json_output(),
  ]);
  command('measure-source runs', 'List the runs of one measure source.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    option('limit', '--limit <INTEGER>', integer(), { default: 30 }),
    option('offset', '--offset <INTEGER>', integer(), { default: 0 }),
    json_output(),
  ]);
  command(
    'measure-source runtime',
    'Show the script helpers, context, and an executable example.',
    [json_output()],
  );
  command('measure-source schema', 'Show a measure-source request schema.', [
    argument('operation', '[OPERATION]', choice(['create', 'script', 'run']), {
      default: 'create',
    }),
    json_output(),
  ]);
  command(
    'measure-source set-responsible',
    'Set who answers for a measure source from a JSON object.',
    [argument('source_id', '<SOURCE_ID>', text, {}), json_input(), json_output()],
  );
  command('measure-source set-schedule', 'Set the daily schedule of a measure source from JSON.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    json_input(),
    json_output(),
  ]);
  command('measure-source show', 'Show the configuration and script revisions of a source.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    json_output(),
  ]);
  command('measure-source status', 'Show the collection health and current activity of a source.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    json_output(),
  ]);
  command('measure-source submit-script', 'Save a new script for a measure source from JSON.', [
    argument('source_id', '<SOURCE_ID>', text, {}),
    json_input(),
    json_output(),
  ]);

  group('page', 'Read and edit the page documents of Managers and the Chief.', 'data');
  command('page edit', 'Replace a Manager or Chief page from a JSON object {page}.', [
    option('manager_id', '--manager <MANAGER_ID>', text, {
      description: "Edit this Manager's own page document.",
    }),
    option('chief', '--chief', boolean, {
      default: false,
      description: 'Edit the Chief page document.',
    }),
    json_input(),
    json_output(),
  ]);
  command('page schema', 'Show the page edit envelope, document schema, and examples.', [
    json_output(),
  ]);
  command('page show', 'Read a saved Manager or Chief page and its page_revision.', [
    option('manager_id', '--manager <MANAGER_ID>', text, {
      description: "Read this Manager's own page document.",
    }),
    option('chief', '--chief', boolean, {
      default: false,
      description: 'Read the Chief page document.',
    }),
    json_output(),
  ]);

  group('preview', "Publish your Ticket's application preview.", 'worker');
  command('preview prepare', 'Reserve a preview prefix before starting your application.', [
    json_output(),
  ]);
  command('preview register', 'Check a loopback listener and return its preview link.', [
    option('port', '--port <INTEGER>', integer(1024, 65535), { required: true }),
    json_output(),
  ]);
  command('preview show', 'Show the current preview and its link.', [json_output()]);
  command('preview stop', 'End the preview and close its open requests.', [json_output()]);

  group('project', 'List, read, create, and change projects.', 'data');
  command('project create', 'Create one project.', [
    option('name', '--name <TEXT>', text, { description: 'Project display name.', required: true }),
    option('summary', '--summary <TEXT>', text, { description: 'Project summary text.' }),
    option('folder_path', '--folder-path <TEXT>', text, {
      description: 'Absolute project folder path.',
    }),
    option('repository_url', '--repository-url <TEXT>', text, {
      description: 'Git repository URL for Ticket checkouts.',
    }),
    option('default_branch', '--default-branch <TEXT>', text, {
      description: 'Default Git branch for Ticket checkouts.',
    }),
    json_output(),
  ]);
  command('project list', 'List projects.', [...page_inputs(), json_output()]);
  command('project set', 'Change one project field.', [
    argument('project_id', '<PROJECT_ID>', text, {}),
    argument(
      'field',
      '<FIELD>',
      choice(['default-branch', 'folder-path', 'name', 'repository-url', 'summary']),
      {},
    ),
    ...text_input('value'),
    option('clear', '--clear', boolean, { default: false, description: 'Clear the field.' }),
    json_output(),
  ]);
  command('project show', 'Read one project or its summary.', [
    argument('project_id', '<PROJECT_ID>', text, {}),
    argument('part_names', '[PART_NAMES...]', text, {
      description: 'Parts to print: summary.',
    }),
    json_output(),
  ]);

  command('recap', 'Write your own recap: your Ticket, your Manager, or the Chief.', text_input(), {
    audience: 'everyone',
  });
  command(
    'review',
    'Show what waits on Khushal, oldest first, as Review shows it.',
    [json_output()],
    {
      audience: 'supervisor',
    },
  );

  command('restart', 'Ask the foreground Panels supervisor to replace its application.', [], {
    audience: 'host',
  });
  command(
    'send-message',
    'Send one message to Khushal, the Chief, a Ticket, or a Manager.',
    [
      option('owner', '--owner', boolean, { default: false, description: 'Send to Khushal.' }),
      option('chief', '--chief', boolean, {
        default: false,
        description: 'Send to the Chief of Staff.',
      }),
      option('ticket_id', '--ticket <TICKET_ID>', text, {
        description: "Send to a Ticket's Worker.",
      }),
      option('manager_id', '--manager <MANAGER_ID>', text, { description: 'Send to a Manager.' }),
      option('mode', '--mode <CHOICE>', choice(['queue', 'steer', 'send_now']), {
        default: 'steer',
        description: 'Steer into the running turn, queue behind it, or interrupt it.',
      }),
      ...text_input(),
      json_output(),
    ],
    { audience: 'everyone' },
  );
  command('serve', 'Start the Panels web server and its background work.', [], {
    audience: 'host',
  });

  group('skill', 'Read and edit the managed skills.', 'config');
  command('skill apply', 'Apply a reviewed skill manifest in one step.', [
    json_input(),
    json_output(),
  ]);
  command(
    'skill check',
    'Report every skill that names a command, Stage, or skill this build lacks.',
    [json_output()],
  );
  command('skill delete', 'Delete a skill and its folder, unless a Worker type uses it.', [
    argument('name', '<NAME>', text, {}),
    json_output(),
  ]);
  command('skill edit', "Change a skill's description or its Markdown body.", [
    argument('name', '<NAME>', text, {}),
    option('description', '--description <TEXT>', text, {
      description: 'The one-line description in the frontmatter.',
    }),
    ...text_input(),
    json_output(),
  ]);
  command('skill list', 'List the managed skill names.', [json_output()]);
  command('skill show', 'Print the Markdown body of a skill.', [
    argument('name', '<NAME>', text, {}),
    json_output(),
  ]);
  command('skill write-file', "Write a supporting file inside a skill's folder.", [
    argument('name', '<NAME>', text, {}),
    argument('path', '<PATH>', text, {}),
    ...text_input(),
    json_output(),
  ]);

  group('stalls', 'Alarm on stalled Panels work.', 'host');
  command('stalls watchdog', 'Alarm once on each open stall and on a stale checker heartbeat.', [
    option('db_path', '--db <FILE>', readable_path('file', true), { required: true }),
    option('state_dir', '--state-dir <DIRECTORY>', readable_path('directory', false), {
      required: true,
    }),
  ]);

  group('ticket', 'Read, decide, create, and steer Tickets.', 'supervisor');
  command('ticket approve', 'Approve the pending proposal of a Ticket and set its next ceiling.', [
    TICKET_ID(),
    option('ceiling', '--ceiling <STAGE>', text, {
      description: 'The next ceiling. Not needed for the final approval.',
    }),
    option('holder', '--holder <HOLDER>', text, {
      description: 'Who holds the next ceiling: me, owner, chief, or a Manager id. Default: me.',
    }),
    option('edit_file', '--edit-file <FILE>', readable_file, {
      description: 'Approve this edited text instead, or - for stdin.',
    }),
    option('kickoff_title', '--kickoff-title <TEXT>', text, {
      description: 'Retitle the Ticket while approving its brief.',
    }),
    option('kickoff_note_file', '--kickoff-note-file <FILE>', readable_file, {
      description: 'Approve the brief with this edited kickoff note, or - for stdin.',
    }),
    option('expected_proposal_revision', '--expected-proposal-revision <INTEGER>', integer(1), {
      description: 'The proposal revision you read. Default: the revision read now.',
    }),
    json_output(),
  ]);
  command('ticket assign', 'Move a Ticket to a Manager or to the Chief.', [
    TICKET_ID(),
    option('chief', '--chief', boolean, { default: false, description: 'Assign to the Chief.' }),
    option('manager_id', '--manager <MANAGER_ID>', text, {
      description: 'Assign to this active Manager.',
    }),
    json_output(),
  ]);
  command('ticket backlog', 'Defer automatic Worker steps and keep the work.', [
    TICKET_ID(),
    json_output(),
  ]);
  command('ticket block', 'Make a Ticket wait for another Ticket.', [
    TICKET_ID(),
    option('blocker_id', '--by <TICKET_ID>', text, {
      description: 'The Ticket it waits for.',
      required: true,
    }),
    json_output(),
  ]);
  command('ticket complete', 'Complete a Stage that Khushal owns with its value.', [
    TICKET_ID(),
    argument('field', '<FIELD>', text, {}),
    ...text_input('value'),
    json_output(),
  ]);
  command('ticket create', 'Create one Ticket from a JSON object.', [
    json_input(),
    option('machine_id', '--machine-id <MACHINE_ID>', text, {
      description: 'Destination from panels machine list. Default: the home.',
    }),
    option('locale', '--locale <CHOICE>', choice(['vps', 'macos']), {
      description: 'Legacy name for the original home or the Mac.',
    }),
    json_output(),
  ]);
  command('ticket delete', 'Delete one Ticket permanently.', [
    TICKET_ID(),
    option('force', '--force', boolean, {
      default: false,
      description: 'Delete a Ticket that still looks running.',
    }),
    json_output(),
  ]);
  command('ticket edit', 'Change Ticket fields from a JSON object.', [
    argument('ticket_id', '[TICKET_ID]', text, { description: 'Default: your own Ticket.' }),
    json_input(),
    json_output(),
  ]);
  command(
    'ticket history',
    "Read the messages in a Ticket's Worker conversation.",
    [
      argument('ticket_id', '[TICKET_ID]', text, {
        envNames: ['PLAN_TICKET_ID'],
        description: 'Default: your own Ticket.',
      }),
      option('limit', '--limit <INTEGER>', integer(1, 100), { default: 30 }),
      option('before', '--before <INTEGER>', integer(), {
        description: 'Read the page before this sequence.',
      }),
      option('after', '--after <INTEGER>', integer(), {
        description: 'Read forward from this sequence.',
      }),
      json_output(),
    ],
    { audience: 'everyone' },
  );
  command(
    'ticket list',
    'List live Tickets and what each needs.',
    [
      option('manager', '--manager <MANAGER_ID>', text, {
        description: "Only this Manager's Tickets.",
      }),
      option('chief', '--chief', boolean, {
        default: false,
        description: "Only the Chief's own Tickets.",
      }),
      option('needs', '--needs <NEED>', choice([...TICKET_NEEDS]), {
        description: `Only Tickets with this need. Repeat for more: ${TICKET_NEEDS.join(', ')}.`,
        repeat: true,
      }),
      option('backlog', '--backlog', boolean, {
        default: false,
        description: 'Show backlogged Tickets instead.',
      }),
      option('done', '--done', boolean, {
        default: false,
        description: 'Show done Tickets instead.',
      }),
      option('all', '--all', boolean, {
        default: false,
        description: 'Show live, backlogged, and done Tickets.',
      }),
      option('worker_type', '--worker-type <TEXT>', text, {
        description: 'Only this Worker type. Repeat for more.',
        repeat: true,
      }),
      option('stage', '--stage <STAGE>', text, {
        description: 'Only this Stage. Repeat for more.',
        repeat: true,
      }),
      option('exclude_stage', '--exclude-stage <STAGE>', text, {
        description: 'Leave out this Stage. Repeat for more.',
        repeat: true,
      }),
      option('search', '--search <TEXT>', text, { description: 'Case-insensitive text search.' }),
      option('project', '--project <TEXT>', text, { description: 'Only this project name.' }),
      option('project_id', '--project-id <TEXT>', text, { description: 'Only this project id.' }),
      ...page_inputs(),
      json_output(),
    ],
    { audience: 'everyone' },
  );
  command(
    'ticket propose',
    'File the proposal for the current Stage of your own Ticket.',
    text_input().concat([json_output()]),
    { audience: 'worker' },
  );
  command(
    'ticket recheck-worker-cleanup',
    'Recheck Worker cleanup without starting work or discarding evidence.',
    [TICKET_ID(), json_output()],
  );
  command('ticket request-recap', 'Ask a Ticket below you for a fresh recap.', [
    TICKET_ID(),
    json_output(),
  ]);
  command('ticket restart-worker', 'Restart the Worker of a Ticket, optionally on another model.', [
    TICKET_ID(),
    option('backend', '--backend <BACKEND>', text, { description: 'Backend to restart on.' }),
    option('model', '--model <MODEL>', text, { description: 'Model on that backend.' }),
    option('reasoning_effort', '--reasoning-effort <TEXT>', text, {
      description: 'Reasoning effort, for a model that takes one.',
    }),
    json_output(),
  ]);
  command('ticket resume', 'Resume automatic Worker steps under the existing gates.', [
    TICKET_ID(),
    json_output(),
  ]);
  command('ticket schema', 'Show the keys that ticket create and ticket edit accept.', [
    argument('operation', '[OPERATION]', choice(['create', 'edit']), {
      description: 'create or edit. Default: both.',
    }),
    json_output(),
  ]);
  command('ticket send-back', 'Send the pending proposal of a Ticket back with guidance.', [
    TICKET_ID(),
    ...text_input(),
    option('expected_proposal_revision', '--expected-proposal-revision <INTEGER>', integer(1), {
      description: 'The proposal revision you read. Default: the revision read now.',
    }),
    json_output(),
  ]);
  command(
    'ticket show',
    'Read a Ticket, or your own, as a header and its parts.',
    [
      argument('ticket_id', '[TICKET_ID]', text, {
        description: 'A Ticket id (t_...). Default: your own Ticket.',
      }),
      argument('part_names', '[PART_NAMES...]', text, {
        description: 'Parts to print. Separate them by spaces or commas.',
      }),
      json_output(),
    ],
    { audience: 'everyone' },
  );
  command('ticket unblock', 'Stop a Ticket waiting for another Ticket.', [
    TICKET_ID(),
    option('blocker_id', '--by <TICKET_ID>', text, {
      description: 'The Ticket it waits for.',
      required: true,
    }),
    json_output(),
  ]);

  group('worker-type', 'Read and declare the Worker types.', 'config');
  command('worker-type delete', 'Remove a Worker type. Its Tickets keep their stages and skill.', [
    argument('worker_type', '<WORKER_TYPE>', text, {}),
    json_output(),
  ]);
  command('worker-type list', 'List the Worker types.', [json_output()]);
  command('worker-type save', 'Declare or replace a Worker type from a JSON record.', [
    json_input(),
    json_output(),
  ]);
  command('worker-type show', 'Print one Worker type in the shape that save takes.', [
    argument('worker_type', '<WORKER_TYPE>', text, {}),
    json_output(),
  ]);
  command(
    'worker-type skill',
    "Change the description or Markdown body of a Worker type's skill.",
    [
      argument('worker_type', '<WORKER_TYPE>', text, {}),
      option('description', '--description <TEXT>', text, {
        description: 'The one-line description in the frontmatter.',
      }),
      ...text_input(),
      json_output(),
    ],
  );
  return root;
}
