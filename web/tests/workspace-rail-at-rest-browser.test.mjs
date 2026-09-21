import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Browser proof that the rail answers what needs the owner while it is at rest. A
// Sprint Item nobody has opened shows the same groups and the same rows an open one
// shows, so the answer costs no click. An Item with nothing for him shows nothing.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = `.workspace-rail-at-rest-${process.pid}`;
const host = join(root, "tests", `${stem}.svelte`);
const main = join(root, "tests", `${stem}.ts`);
const index = join(root, "tests", `${stem}.html`);
let server;

try {
  await writeFile(host, `<script lang="ts">
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import BoardRoute from "../src/routes/BoardRoute.svelte";

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const address = { selection: { kind: "none" as const }, view: "items" as const, openFile: null };
</script>

<QueryClientProvider {client}>
  <BoardRoute {address} />
</QueryClientProvider>
`, "utf8");
  await writeFile(
    main,
    `import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host, { target: document.getElementById("app")! });`,
    "utf8"
  );
  await writeFile(
    index,
    `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`,
    "utf8"
  );

  server = await createServer({
    root,
    configFile: false,
    plugins: [svelte()],
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error"
  });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === "object");

  const script = String.raw`
import json, sys
from playwright.sync_api import sync_playwright, expect

def card(ticket_id, item_id, item_title, **values):
    row = {
        "id": ticket_id, "title": ticket_id, "priority": "P1", "deadline": None,
        "project_id": None, "project": None, "group_project_id": None, "group_project": None,
        "activity_at": 0, "has_pending_proposal": False, "ticket_status": "empty",
        "worker_type": "coding", "employee_backend": "codex", "stage": "needs_implementation",
        "stage_label": "Implementation", "gating_field": "implementation",
        "gating_field_label": "Implementation", "is_done": False, "blocked": False,
        "conversation_id": None, "waiting_to_closeout": False,
        "sprint_item_id": item_id, "sprint_item_title": item_title, "sprint_item_priority": "P1",
        "awaiting_reply": False, "awaiting_answer": False, "awaiting_approval": False,
        "awaiting_agent_approval": False, "assigned": False, "agent_state": "idle",
    }
    row.update(values)
    return row

def item(item_id):
    quiet = {
        "awaiting_reply": False, "awaiting_answer": False, "awaiting_approval": False,
        "awaiting_agent_approval": False, "assigned": False, "agent_state": "idle",
    }
    return {"id": item_id, "created_at": 0, "conversation_id": None, "ticket_rollup": dict(quiet), **quiet}

# One Item holds all four of his groups plus a Ticket that is only an agent's to approve.
# The other holds nothing that is his.
board = {
    "columns": [{"stage": "needs_implementation", "cards": [
        card("t_approval", "si_needs_him", "Needs him", awaiting_approval=True),
        card("t_assigned", "si_needs_him", "Needs him", assigned=True),
        card("t_message", "si_needs_him", "Needs him", awaiting_reply=True),
        card("t_answer", "si_needs_him", "Needs him", awaiting_answer=True),
        card("t_agents_approval", "si_needs_him", "Needs him",
             ticket_status="awaiting_approval", awaiting_agent_approval=True),
        # A broken worker still holding a proposal for him. It is his to approve, and the
        # Item at rest keeps the three groups, so it belongs under Needs your approval.
        card("t_broken", "si_needs_him", "Needs him", awaiting_approval=True, agent_state="errored"),
        card("t_quiet", "si_quiet", "Quiet", agent_state="working"),
    ]}],
    "sprint_items": [item("si_needs_him"), item("si_quiet")],
}
workers = {
    "chief_of_staff": {"label": "Chief of Staff", "needs_me": False, "agent_working": False, "latest_turn_ended_sequence": 0},
    "workers": [],
}

def respond(route):
    path = route.request.url.split("/api/", 1)[1].split("?")[0]
    if path == "board": result = board
    elif path == "workers": result = workers
    else: result = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.route("**/api/**", respond)
    page.goto(sys.argv[1])

    needs_him = page.locator('[data-sprint-item="si_needs_him"]')
    needs_him.wait_for()

    # Nothing is selected, and the groups are already there.
    assert page.locator('[data-sprint-item] [aria-current="page"]').count() == 0
    assert page.locator(".board-workspace-item--selected").count() == 0

    # Exactly the four groups. A broken worker does not open a fifth one here. An answer
    # he owes sits under the decision he owes and above the Stage that is merely his.
    headers = needs_him.locator("[data-bucket-key]")
    expect(headers).to_have_count(4)
    assert [headers.nth(i).get_attribute("data-bucket-key") for i in range(4)] == [
        "awaiting_approval", "awaiting_answer", "assigned", "awaiting_reply"
    ]
    assert [
        headers.nth(i).locator(".board-workspace-bucket-label").inner_text() for i in range(4)
    ] == ["Needs your approval", "Needs your answer", "Yours", "Messages"]

    # The rows are there too, one Ticket under one header.
    for ticket_id, key in [
        ("t_approval", "awaiting_approval"),
        ("t_broken", "awaiting_approval"),
        ("t_assigned", "assigned"),
        ("t_message", "awaiting_reply"),
        ("t_answer", "awaiting_answer"),
    ]:
        row = needs_him.locator('[data-ticket-id="' + ticket_id + '"]')
        expect(row).to_have_count(1)
        assert row.is_visible()
        assert needs_him.locator('[data-bucket-key="' + key + '"] [data-ticket-id="' + ticket_id + '"]').count() == 1

    # A proposal held by an agent is not his, and never reaches the rail's Items.
    assert needs_him.locator('[data-ticket-id="t_agents_approval"]').count() == 0
    assert needs_him.locator('[data-bucket-key="status_awaiting_approval"]').count() == 0
    assert "Awaiting an agent's approval" not in needs_him.inner_text()

    # An Item with none of the three is its title and its mark, and nothing else.
    quiet = page.locator('[data-sprint-item="si_quiet"]')
    assert quiet.locator("[data-bucket-key]").count() == 0
    assert quiet.locator("[data-ticket-id]").count() == 0

    # Selection is the card. No count line stands in for the groups any more.
    assert page.locator(".board-workspace-item-line").count() == 0
    browser.close()
`;
  const child = spawn(
    join(root, "..", ".venv", "bin", "python"),
    ["-c", script, `http://127.0.0.1:${address.port}/tests/${stem}.html`],
    { stdio: "inherit" }
  );
  const code = await new Promise((resolve) => child.on("exit", resolve));
  assert.equal(code, 0, "Workspace rail at-rest browser proof failed");
} finally {
  await server?.close();
  await Promise.all([host, main, index].map((path) => rm(path, { force: true })));
}

console.log("workspace-rail-at-rest-browser.test.mjs: all assertions passed");
