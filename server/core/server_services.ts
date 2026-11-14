import type { FastifyInstance, FastifyListenOptions } from 'fastify';
import { createAreasRuntime } from '../areas.ts';
import { createApp } from '../app.ts';
import { buildClock } from './clock.ts';
import type { Config } from './config.ts';
import { ChangeSignal } from './changes/index.ts';
import { createDatabase } from './database/index.ts';
import { require_application_schema } from './database/admission.ts';
import { notify_chief as notify_chief_of_integrity_faults } from '../tickets/integrity_notice.ts';
import { acquireLock } from './locks/index.ts';
import type { OwnedAreaContext } from './authority/register.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { createConversationRoutes } from '../conversation/routes.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import { createBackendRoutes } from '../conversation/snapshot/routes.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../conversation/snapshot/composition.ts';
import { CredentialRequestOperations } from '../credentials/requests/service.ts';
import { createCredentialRequestRoutes } from '../credentials/requests/routes.ts';
import { makeUsageService } from '../conversation/usage/composition.ts';
import { createUsageRoutes } from '../conversation/usage/routes.ts';
import type { RecordedUsageService } from '../conversation/usage/contracts.ts';
import { run_automatic_recovery } from '../conversation/deploy_interruptions/coordinator.ts';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { createVideoUploadRoutes } from '../conversation/video_uploads/routes.ts';
import { createVoiceRoutes } from '../conversation/voice_transcription/routes.ts';
import { CLEANUP_INTERVAL_SECONDS } from '../conversation/video_uploads/configuration.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import type { EnvironmentRuntime } from '../environments/contracts.ts';
import { HttpRequestScope } from './http/lifetime.ts';
import { is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  StopOptions,
  ComposedBackgroundService,
} from '../lifecycle/contracts.ts';
import {
  compose_background_services,
  dispatcher_admitted,
  reconcile_background_attention,
} from '../lifecycle/background.ts';
import type {
  ConversationCompositionContext,
  TestCompositionContext,
  ServerOptions,
} from './start.ts';
import type { VpsExecutor } from '../executor/vps_transport.ts';
import { ServiceResources } from './service_resources.ts';

interface ServerLifetime {
  media_lifetime: AbortController;
  request_lifetime: HttpRequestScope;
  child_processes: OwnedChildProcesses;
  one_shot_children: OwnedOneShotChildren;
  isClosing(): boolean;
  shutdown_options(): StopOptions;
}

/** Service constructors reserve custody before any factory or asynchronous continuation. */
export class ServerServices {
  readonly resources = new ServiceResources();
  readonly environments = this.resources.service<EnvironmentRuntime>(
    'environment services',
    (value, stop) => value.close(stop),
    (value) => value.settled(),
  );
  readonly areas = createAreasRuntime(() => this.environments.value?.resourceProviders ?? []);
  readonly conversationWrites = new ConversationHttpWriteScope();
  readonly recorded_usage = this.resources.service<RecordedUsageService>(
    'recorded usage',
    (value, stop) => value.stop?.(stop),
    (value) => value.settled(),
  );
  readonly background = this.resources.service<ComposedBackgroundService>(
    'background services',
    (value, stop) => value.close(stop),
    (value) => value.settled(),
  );
  readonly backend = this.resources.service<BackendHttpRuntime>(
    'backend services',
    (value, stop) => value.shutdown?.(stop),
    (value) => value.settled(),
  );
  readonly executor = this.resources.service<VpsExecutor>(
    'executor services',
    (value, stop) => value.stop(stop),
    (value) => value.settled(),
  );
  readonly core = this.resources.service<SqliteProcessConversationSystem>(
    'conversation core',
    (value, stop) => value.shutdown(stop),
    (value) => value.settled(),
  );
  readonly video = this.resources.service<VideoUploadService>(
    'video transfers',
    (value, stop) => value.drain(stop),
    (value) => value.settled(),
  );
  readonly app = this.resources.service<FastifyInstance>('HTTP server', (value) => value.close());
  readonly credentials = this.resources.service<CredentialRequestOperations>(
    'credential requests',
    (value, stop) => value.finish_operations(stop),
    (value) => value.settled(),
  );
  database: ReturnType<typeof createDatabase> | undefined;
  dispatcher: ReturnType<typeof acquireLock> = null;
  publication: SignalPublicationCoordinator | undefined;
  environmentsStarting: Promise<void> | undefined;
  video_cleanup_timer: ReturnType<typeof setInterval> | undefined;
  video_cleanup_work: Promise<unknown> | undefined;
  recovery: Promise<void> | undefined;
  #ownership_current = true;
  #signalRevoked = false;
  readonly config: Config;
  readonly options: ServerOptions;
  readonly lifetime: ServerLifetime;

  constructor(config: Config, options: ServerOptions, lifetime: ServerLifetime) {
    this.config = config;
    this.options = options;
    this.lifetime = lifetime;
  }

  requireOwnership = (): void => {
    if (!this.#ownership_current) throw new Error('the conversation runtime is shut down');
  };
  revokeSignal = (): void => {
    if (this.#signalRevoked) return;
    this.#signalRevoked = true;
    this.publication?.invalidate('shutdown');
  };
  close_database(): void {
    this.database?.close();
    this.#ownership_current = false;
  }
  assert_startup_open(): void {
    if (this.lifetime.isClosing()) throw new Error('Server closed during startup');
  }

  create_record() {
    this.assert_startup_open();
    const { config, options, areas } = this;
    const { media_lifetime, one_shot_children, child_processes } = this.lifetime;
    const changes = new ChangeSignal();
    const clock = buildClock(config);
    require_application_schema(config.db_path);
    const database = createDatabase(config.db_path, {
      commitSteps: areas.commitSteps,
      onChange: () => changes.emit(),
    });
    this.database = database;
    const publication = new SignalPublicationCoordinator({
      database,
      require_runtime_ownership: this.requireOwnership,
    });
    this.publication = publication;
    areas.bindPublication(publication);
    const integrity_issues = areas.prepare(database);
    if (!config.test_mode && (config.dispatch_enabled || config.notifications_enabled))
      this.dispatcher = acquireLock(config.dispatcher_lock_path);
    const executor =
      options.createExecutor?.({
        database,
        config,
        clock,
        changes,
        media_signal: media_lifetime.signal,
        one_shot_children,
        child_processes,
      }) ?? null;
    this.executor.own(executor ?? undefined);
    const before_record: Omit<ConversationCompositionContext, 'record'> = {
      database,
      config,
      clock,
      changes,
      publication,
      require_runtime_ownership: this.requireOwnership,
      revoke_signal: this.revokeSignal,
      one_shot_children,
      child_processes,
      executor,
    };
    const open_runs = options.readOpenRuns?.(before_record) ?? new Map();
    areas.initialize(
      database,
      config,
      clock,
      () => {
        if (dispatcher_admitted(config, this.dispatcher !== null))
          reconcile_background_attention({ database });
      },
      open_runs,
    );
    const context: ConversationCompositionContext = {
      ...before_record,
      record: areas.getRuntime(),
    };
    return { context, integrity_issues };
  }

  create_conversation(context: ConversationCompositionContext) {
    const { options, config, areas, conversationWrites } = this;
    const { database, clock, publication, one_shot_children } = context;
    const { media_lifetime } = this.lifetime;
    const core = options.createConversationCore?.(context);
    this.core.own(core);
    this.assert_startup_open();
    const credentials = core
      ? new CredentialRequestOperations({
          database,
          core,
          writes: conversationWrites,
          key_path: config.credential_key_path,
          now: () => clock.nowUnix(),
        })
      : undefined;
    this.credentials.own(credentials);
    const uploads = new VideoUploadService(database, config.db_path, {
      signal: media_lifetime.signal,
    });
    this.video.own(uploads);
    const conversation_http = core
      ? { core, record: context.record, writes: conversationWrites }
      : null;
    const backendServices =
      options.createBackendServices?.(context) ??
      create_backend_runtime({
        database,
        config,
        now: () => clock.now(),
        providers: unavailable_backend_providers(),
        one_shot_children,
      });
    this.backend.own(backendServices);
    this.assert_startup_open();
    const conversationSystem = options.conversationSystem ?? core;
    const usage =
      options.createUsageService?.(context) ?? makeUsageService(config, { now: () => clock.now() });
    this.recorded_usage.own(usage);
    this.assert_startup_open();
    if (core)
      areas.bindRuntimeFacts(async (conversation_id) => ({
        running: await core.is_running(conversation_id),
        permissions: [...(await core.pending_permission_ask_ids(conversation_id))],
        inputs: [...(await core.pending_user_input_request_ids(conversation_id))],
      }));
    publication.bind_runtime((conversation_id) => core?.runtime_turn(conversation_id) ?? null);
    options.beforePublication?.(database);
    this.assert_startup_open();
    publication.initialize();
    areas.attachNavigation();
    const testContext: TestCompositionContext = {
      ...context,
      shutdown_options: this.lifetime.shutdown_options,
      core,
      conversationSystem,
      backendServices,
    };
    return {
      core,
      credentials,
      uploads,
      conversation_http,
      backendServices,
      conversationSystem,
      usage,
      testContext,
    };
  }

  async create_environments(context: TestCompositionContext) {
    const environments = await this.options.createEnvironments?.(context);
    this.environments.own(environments);
    this.assert_startup_open();
    const executor = this.executor.value;
    if (executor) {
      if (!environments?.mac_updates)
        throw new Error('Configured executor requires the invocation Mac update service.');
      executor.bind_updates(environments.mac_updates);
    }
    return environments;
  }

  create_background(context: TestCompositionContext, environments: EnvironmentRuntime | undefined) {
    const { options } = this;
    const ownedContext: OwnedAreaContext = {
      database: context.database,
      config: context.config,
      clock: context.clock,
      changes: context.changes,
      signalAuthority: context.publication,
      conversationSystem: context.conversationSystem,
      assert_database_owned: context.require_runtime_ownership,
    };
    const background = compose_background_services(ownedContext, {
      dispatcher_owned: this.dispatcher !== null,
      enable_worker_readiness: options.enableWorkerReadiness,
      before_poll: environments
        ? () => {
            const environmentHold = environments.before_worker_readiness_poll();
            const explicitHold = options.beforeWorkerReadinessPoll?.() ?? false;
            return environmentHold || explicitHold;
          }
        : options.beforeWorkerReadinessPoll,
      areaLoops: options.areaLoops,
      notificationDelivery: options.notificationDelivery,
      stallChecker: options.stallChecker,
    });
    this.background.own(background);
    return background;
  }

  async create_http(
    context: ConversationCompositionContext,
    conversation: ReturnType<ServerServices['create_conversation']>,
    environments: EnvironmentRuntime | undefined,
  ) {
    const { options, config, areas, conversationWrites } = this;
    const { database, changes, clock, publication } = context;
    const {
      core,
      credentials,
      uploads,
      conversation_http,
      backendServices,
      conversationSystem,
      usage,
      testContext,
    } = conversation;
    const { media_lifetime, request_lifetime } = this.lifetime;
    const executor = this.executor.value ?? null;
    this.assert_startup_open();
    const testAreas =
      typeof options.testAreas === 'function'
        ? options.testAreas(testContext)
        : (options.testAreas ?? []);
    this.assert_startup_open();
    const background = this.create_background(testContext, environments);
    const app = await createApp(config, {
      own_app: (created) => {
        this.app.own(created);
      },
      request_lifetime,
      database,
      changes,
      areas: [
        ...areas.areas,
        { prefix: '/api', routes: createCredentialRequestRoutes(credentials ?? null) },
        {
          prefix: '/api/conversation/usage',
          routes: createUsageRoutes({
            conversation_runtime_available: conversationSystem !== undefined,
            recorded_usage: {
              readSummary: async (input) =>
                await conversationWrites.run(() => usage.readSummary(input)),
              refreshRates: async () => await conversationWrites.run(() => usage.refreshRates()),
              finish_operations: (stop) => usage.finish_operations(stop),
              settled: () => usage.settled(),
            },
            backend_state: backendServices.backend_state,
          }),
        },
        {
          prefix: '/api/conversation/video-uploads',
          routes: createVideoUploadRoutes(uploads, conversation_http, media_lifetime.signal),
        },
        {
          prefix: '/api/conversation',
          routes: createVoiceRoutes({ signal: media_lifetime.signal, writes: conversationWrites }),
        },
        ...(environments?.areas ?? []),
        {
          prefix: '/api/conversation',
          routes: createBackendRoutes(backendServices, {
            available: conversationSystem !== undefined,
            writes: conversationWrites,
          }),
        },
        ...(core
          ? [{ prefix: '/api/conversation', routes: createConversationRoutes(conversation_http!) }]
          : []),
        ...testAreas,
      ],
      clock,
      signalAuthority: publication,
      conversationSystem,
      workspaceAdmission: conversationSystem?.workspace_admission,
      executor,
    });
    this.app.own(app);
    this.assert_startup_open();
    if (executor) {
      if (!core) throw new Error('Configured executor requires the invocation conversation core.');
      executor.configure(core, app);
    }
    await app.ready();
    this.assert_startup_open();
    return { app, background };
  }

  start_recovery(
    context: ConversationCompositionContext,
    conversation: ReturnType<ServerServices['create_conversation']>,
    integrity_issues: ReturnType<ReturnType<typeof createAreasRuntime>['prepare']>,
  ): void {
    const { core, uploads } = conversation;
    if (!core) return;
    uploads.recover_and_cleanup();
    this.assert_startup_open();
    this.video_cleanup_timer = setInterval(() => {
      if (this.lifetime.isClosing() || this.video_cleanup_work !== undefined) return;
      this.video_cleanup_work = core.workspace_admission.operation(async (admitted) => {
        if (admitted) uploads.recover_and_cleanup();
      });
      void this.video_cleanup_work
        .catch((error) => {
          console.error('Video upload cleanup failed', error);
        })
        .finally(() => {
          this.video_cleanup_work = undefined;
        });
    }, CLEANUP_INTERVAL_SECONDS * 1000);
    this.video_cleanup_timer.unref();
    const recovery = Promise.resolve().then(() =>
      core.workspace_admission.operation(async (admitted) => {
        if (!admitted) return;
        await notify_chief_of_integrity_faults(
          context.database,
          core,
          context.clock,
          integrity_issues,
        );
        if (core.workspace_admission.closed) return;
        await core.tell_agents_of_background_tasks_lost_at_restart();
        if (core.workspace_admission.closed) return;
        await run_automatic_recovery(core, context.database);
      }),
    );
    this.recovery = recovery;
    void recovery.then(
      () => {
        if (this.recovery === recovery) this.recovery = undefined;
      },
      (error) => {
        if (!is_shutdown_business_cancellation(error))
          console.error('Conversation startup recovery failed', error);
        if (this.recovery === recovery) this.recovery = undefined;
      },
    );
  }

  async setup(): Promise<string> {
    const { context, integrity_issues } = this.create_record();
    const conversation = this.create_conversation(context);
    const environments = await this.create_environments(conversation.testContext);
    this.assert_startup_open();
    const { app, background } = await this.create_http(context, conversation, environments);
    this.assert_startup_open();
    await this.executor.value?.start();
    this.assert_startup_open();
    this.areas.startRecordReplay(conversation.core?.workspace_admission);
    this.assert_startup_open();
    await this.areas.startRouteServices();
    this.assert_startup_open();
    this.start_recovery(context, conversation, integrity_issues);
    this.environmentsStarting = Promise.resolve().then(() => {
      if (!this.lifetime.isClosing()) return environments?.start();
    });
    await this.environmentsStarting;
    this.assert_startup_open();
    if (conversation.core && !this.config.test_mode) {
      await conversation.core.start_idle_child_janitor();
      this.assert_startup_open();
    }
    await background.start();
    this.assert_startup_open();
    const inherited = process.env.PLAN_SERVER_LISTENER_FD;
    if (inherited !== undefined) {
      const fd = Number(inherited);
      if (!Number.isInteger(fd) || fd < 0) throw new Error('invalid PLAN_SERVER_LISTENER_FD');
      return app.listen({ fd } as FastifyListenOptions & { fd: number });
    }
    return app.listen({ host: '127.0.0.1', port: this.config.port });
  }
}
