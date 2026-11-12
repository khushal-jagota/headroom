/** The existing fictional workspace, built by current TS domain writers. */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { ApplicationSchemaSeed } from '../core/database/contracts.ts';
import {
  createDatabase,
  createDatabaseFromSeed,
  checkpointDatabase,
} from '../core/database/index.ts';
import type { Clock } from '../core/clock.ts';
import { OWNER } from '../core/contracts.ts';
import { createProject } from '../projects/data/index.ts';
import { create_manager } from '../managers/data.ts';
import {
  create_ticket,
  file_current_proposal,
  edit_ticket,
  mark_ticket_errored,
} from '../tickets/data/index.ts';
import { TITLE_MAX_CHARS } from '../tickets/contracts.ts';
import { filesRoot } from '../files/logic/paths.ts';
import { finalize as artifactLinks } from '../files/catalog/links.ts';
import { createNavigationRegistry } from '../navigation/data/registry.ts';
import { with_worker_runtime_definitions } from '../worker_types/configuration.ts';
import { write_skill_home } from '../skills/sources.ts';
import { bootstrap_application } from './bootstrap.ts';
import type { FakeFixtureReport } from './fixture_contracts.ts';
import { FAKE_FIXTURE_VERSION } from './registry/store.ts';

export function build_fake_environment_database(path: string, now: number): FakeFixtureReport {
  mkdirSync(dirname(path), { recursive: true });
  const seed = JSON.parse(
    readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
  ) as ApplicationSchemaSeed;
  const fresh = createDatabaseFromSeed(path, seed, (tx) =>
    bootstrap_application(tx, Math.floor(Date.now() / 1000)),
  );
  fresh.close();
  const navigation = createNavigationRegistry();
  const database = createDatabase(path, { commitSteps: [artifactLinks, navigation.finalize] });
  const date = new Date(now * 1000);
  const clock: Clock = { now: () => date, nowUnix: () => now, isoformat: () => date.toISOString() };
  try {
    write_skill_home(database, dirname(path));
    const { projects, managers, tickets } = database.read((sql) =>
      with_worker_runtime_definitions(sql, () => {
        const projects = [
          ['Northstar Demo', 'Fictional product workspace for environment isolation.'],
          ['Harbor Ops', 'Fictional operations workspace for staging testing.'],
        ].map(([name, summary]) =>
          database.write((tx) => createProject(tx, { name, summary }, now)),
        );
        const managers = [
          [
            'Prepare isolated runtime story',
            'Small representative plan for fictional staging work.',
          ],
          [
            'Review fake environment behavior',
            'Fictional review item with child tickets in multiple states.',
          ],
        ].map(([title, goal], index) =>
          database.write((tx) =>
            create_manager(tx, { title, goal, project_id: projects[index].id, clock }),
          ),
        );
        const specifications = [
          {
            title: 'Implement fake environment materialization',
            brief: 'Build fictional state only.',
            worker_type: 'coding',
            manager_id: managers[0].id,
            stated_ceiling: 'needs_what_changes',
            proposal: 'Staging data is isolated from live data.',
            recap: 'The fake fixture has a settled kickoff and success note.',
          },
          {
            title: 'Sketch fictional specialist onboarding',
            brief: 'Invent a representative worker without creating registry rows.',
            worker_type: 'new_worker',
            manager_id: managers[0].id,
            stated_ceiling: 'needs_stages',
            proposal: 'Use existing registered Worker types only.',
            recap: 'Onboarding is represented by a current registry type.',
          },
          {
            title: 'Compare staging reset outcomes',
            brief: 'Inspect reset behavior in fictional staging.',
            worker_type: 'exploration',
            manager_id: managers[1].id,
            stated_ceiling: 'needs_research_plan',
            proposal: 'The reset should replace data only for that instance.',
            recap: 'Exploration is ready for a research plan.',
          },
          {
            title: 'Draft fictional initiative outline',
            brief: 'Keep this pending to show approval state.',
            worker_type: 'initiative_planning',
            project_id: projects[0].id,
          },
        ];
        const tickets = specifications.map((specification) => {
          let ticket = database.write((tx) =>
            create_ticket(tx, {
              ...specification,
              principal: OWNER,
              now,
              title_max_chars: TITLE_MAX_CHARS,
            }),
          );
          if ('proposal' in specification) {
            const principal = { kind: 'ticket' as const, id: ticket.id };
            ticket = database.write((tx) =>
              file_current_proposal(tx, ticket.id, {
                body: specification.proposal!,
                principal,
                now,
              }),
            );
            ticket = database.write((tx) =>
              edit_ticket(tx, ticket.id, {
                edit: { recap: specification.recap },
                title_max_chars: TITLE_MAX_CHARS,
                principal,
                now,
              }),
            );
          }
          return ticket;
        });
        tickets[2] = database.write((tx) =>
          mark_ticket_errored(tx, tickets[2].id, {
            now,
            error_reason: 'could not start (backend_did_not_start)',
          }),
        );
        return { projects, managers, tickets };
      }),
    );
    const ticket_root = join(filesRoot(path, 'ticket'), tickets[0].id);
    mkdirSync(ticket_root, { recursive: true });
    writeFileSync(
      join(ticket_root, 'fixture-note.md'),
      '# Fictional Managed Note\n\nThis file belongs only to the fake fixture.\n',
      'utf8',
    );
    writeFileSync(
      join(ticket_root, 'placeholder.png'),
      Buffer.from(
        '89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de' +
          '0000000c49444154789c63606060000000040001f61738550000000049454e44ae426082',
        'hex',
      ),
    );
    checkpointDatabase(database, 'TRUNCATE');
    return {
      fixture_version: FAKE_FIXTURE_VERSION,
      logical_summary: { projects: 2, managers: 2, tickets: 4, managed_files: 2 },
      logical_titles: {
        projects: projects.map((project) => project.name),
        managers: managers.map((manager) => manager.title),
        tickets: tickets.map((ticket) => ticket.title),
      },
      generated_ids: new Set([
        ...managers.map((manager) => manager.id),
        ...tickets.map((ticket) => ticket.id),
      ]),
      managed_file_relative_paths: [
        `tickets/${tickets[0].id}/fixture-note.md`,
        `tickets/${tickets[0].id}/placeholder.png`,
      ],
    };
  } finally {
    database.close();
  }
}
