import { expect, test } from 'vitest';
import { create, fixture } from './ticket_fixture.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import { createSignalNavigationRuntime } from '../navigation/index.ts';

// Distinct gap: the navigation step recounts totals only when a conversation's
// addresses change. No trigger marks the totals for an association row, so this
// checks that gaining and losing one is still counted after that skip. Reuses the Ticket
// fixture and the real producer and navigation commit steps. No server or browser.
test('an event leaves the totals alone and an association change still recounts them', () => {
  let producer: SignalPublicationCoordinator | undefined;
  let navigation: ReturnType<typeof createSignalNavigationRuntime> | undefined;
  const database = fixture({
    commitSteps: [
      (tx) => producer?.finalize_signal(tx),
      (tx) => navigation?.navigationCommitStep(tx),
    ],
  });
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'view-epoch',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    for (const id of ['chief_conversation', 'ticket_conversation'])
      tx.run(
        `INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,created_at)
        VALUES(?,'codex','fixture','/synthetic','full',0)`,
        [id],
      );
    tx.run(
      "INSERT INTO agents(agent_key,conversation_id) VALUES('chief_of_staff','chief_conversation')",
    );
  });
  const ticket = create(database);
  producer = new SignalPublicationCoordinator({ database, require_runtime_ownership: () => {} });
  producer.bind_runtime(() => null);
  producer.initialize();
  navigation = createSignalNavigationRuntime(producer);
  navigation.attach();
  database.write((tx) =>
    tx.run("UPDATE tickets SET conversation_id='ticket_conversation' WHERE id=?", [ticket.id]),
  );
  const conversations = (id: string) =>
    database.read(
      (sql) =>
        JSON.parse(
          sql.get<{ value: string }>(
            "SELECT value FROM navigation_records WHERE kind='totals' AND id=?",
            [id],
          )?.value ?? '{"distinct_conversation_count":0}',
        ).distinct_conversation_count as number,
    );
  expect(conversations('chief:chief')).toBe(2);

  database.write((tx) => {
    tx.run(
      "INSERT INTO conversation_events VALUES('ticket_conversation',1,'agent_message','{}',0)",
    );
    tx.run(
      "UPDATE conversations SET latest_sequence=1 WHERE conversation_id='ticket_conversation'",
    );
  });
  expect(conversations('chief:chief')).toBe(2);

  // Only the association row changes, so only the address comparison can recount.
  database.write((tx) => {
    tx.run(
      `INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,created_at)
      VALUES('earlier_conversation','codex','fixture','/synthetic','full',0)`,
    );
    tx.run("INSERT INTO ticket_conversations VALUES('earlier_conversation',?)", [ticket.id]);
  });
  expect(conversations('chief:chief')).toBe(3);
  database.write((tx) =>
    tx.run("DELETE FROM ticket_conversations WHERE conversation_id='earlier_conversation'"),
  );
  expect(conversations('chief:chief')).toBe(2);
  navigation.close();
});
