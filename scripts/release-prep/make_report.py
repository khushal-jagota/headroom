import json, html, os, sys
A = os.environ["PLAN_TICKET_FILES_DIR"] + "/artifacts"
titles = {r["id"]: r["title"] for r in json.load(open(sys.argv[1]))}
J = lambda p: json.load(open(f"{A}/{p}"))
prep, adm, rd, sch, snap = J("preparation.json"), J("package-admission.json"), J("readiness.json"), J("schema-timing.json"), J("snapshot-flow.json")
old = J("records/old-flow-attempt.json"); br = J("browser/browser-results.json"); dl = J("delivery.json"); cli = J("delivery-cli-route.json")
fs, ss, cmp_ = J("final-stop.json"), J("stop-for-snapshot.json"), J("live-comparison.json")
import datetime as d
old_s = (d.datetime.fromisoformat(old["steps"]["backup"].replace("Z", "+00:00")) - d.datetime.fromisoformat(old["steps"]["rehearsal_prepared"])).total_seconds()
sec = snap["attempt_record"]["snapshot_seconds"]
f = lambda x: "—" if x is None else f"{x:.3f}"
rows = "".join(f"<tr><td>{r['kind']}</td><td>{html.escape(titles.get(r['id'], 'Chief of Staff' if r['kind']=='chief' else 'Review' if r['kind']=='review' else r['id']))}</td><td>{f(r.get('complete_seconds'))}</td><td>{f(r.get('stream_snapshot_seconds'))}</td><td>{'PASS' if r['pass'] else 'FAIL'}</td></tr>" for r in br["results"])
drows = "".join(f"<tr><td>{r['kind']}</td><td>{html.escape(titles.get(r['id'], 'Chief of Staff'))}</td><td>HTTP {r['http']}: {html.escape(r['answer']['error']['message'] if 'error' in r['answer'] else json.dumps(r['answer']))}</td><td>{r['answer_seconds']*1000:.0f} ms</td><td>{r['outcome_after_60s']['body']['outcome']['outcome']} · {r['outcome_after_60s']['body']['outcome']['refusal_reason']}</td></tr>" for r in dl)
srows = "".join(f"<tr><td><code>{s['revision']}</code></td><td>{s['step_seconds']*1000:.1f}</td><td>{s['apply_seconds']*1000:.1f}</td><td>{s['scoped_fk_check_seconds']*1000:.2f}</td><td>{s['scoped_integrity_check_seconds']*1000:.2f}</td><td>{s['seed_schema_check_seconds']*1000:.1f}</td><td>{s['commit_seconds']*1000:.1f}</td></tr>" for s in sch["steps"])
mgr = [r for r in br["results"] if r["kind"] == "manager"]
page = f"""<!doctype html><meta charset=utf-8><title>Release rehearsal e40325811</title>
<style>body{{font:16px/1.5 system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#172128}}table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}td,th{{text-align:left;padding:6px 10px;border-bottom:1px solid #ddd}}th{{background:#f6f7f8}}code{{font-size:14px}}h2{{margin-top:36px}}.k{{font-weight:600}}</style>
<h1>Release rehearsal: staging e40325811 on a copy of live</h1>
<p>Candidate <code>e40325811ca0f6344aebc1a0403898a208506263</code> upgraded a sanitized copy of live from <code>ts_0016</code> to <code>ts_0018</code>. It served all 86 browser targets with no failure. Live stayed on 3d0c534 and was not touched. The copy is deleted.</p>
<p class=k>The update into this release still uses the old snapshot flow.</p>
<p>The running release runs the update, and live 3d0c534 has no staged-copy step. So this update takes a full copy of the stopped database inside the downtime. On the copy, 3d0c534's own snapshot entry took <b>{old_s:.1f} s</b>; live's last real update took 41.0 s for the same step. The new flow first runs on the update after this one. On the copy, it cut the stopped part to <b>{sec['catch_up']:.1f} s</b>.</p>
<h2>Downtime this release will cost (measured on the copy)</h2>
<table><tr><th>Part</th><th>Seconds</th><th>Source</th></tr>
<tr><td>Snapshot: 3d0c534 stopped full copy and check</td><td>{old_s:.1f}</td><td>3d0c534's own <code>snapshot_main.ts</code>, from a copy of the live release</td></tr>
<tr><td>Schema steps ts_0017 and ts_0018, with their checks</td><td>{sch['total_seconds']:.2f}</td><td>candidate <code>schema_step_main.ts</code>, timed in-process</td></tr>
<tr><td>Launch until ready health</td><td>{rd['health_seconds']:.1f}</td><td>sealed candidate, foreground launcher</td></tr>
<tr><td>Launch until Chief view answers 200 and streams a snapshot</td><td>{rd['chief_stream_snapshot_seconds']:.1f}</td><td>no "Conversation view is upgrading" answer seen</td></tr></table>
<p>Startup's conversation reconciliation took 13.7 s, then 13.2 s on the second start, about 325,000 rows each time. The last rehearsal recorded 12.8 s.</p>
<h2>Schema steps</h2>
<table><tr><th>Step</th><th>Whole step (ms)</th><th>Apply</th><th>Scoped FK check</th><th>Scoped integrity check</th><th>Seed schema check</th><th>Commit</th></tr>{srows}</table>
<p>The scoped integrity checks read only the tables each step touched: {', '.join(sch['steps'][0]['integrity_tables'])}; then {', '.join(sch['steps'][1]['integrity_tables'])}. The last rehearsal's full-file check made ts_0016 take 57.0 s. No view rebuild ran: all 2,904 conversation views stayed at projection version 6.</p>
<h2>Update snapshot, new flow (PR 1333)</h2>
<p>The candidate's three snapshot entry points ran against the copy in the installer's order. The real installer cannot run isolated, because it drives <code>systemctl --user</code> and restarts timers. The update record's <code>snapshot_seconds</code>:</p>
<table><tr><th>Part</th><th>Seconds</th><th>When</th></tr>
<tr><td>staged_copy (copy plus full check)</td><td>{sec['staged_copy']:.1f}</td><td>home serving</td></tr>
<tr><td>catch_up</td><td>{sec['catch_up']:.1f}</td><td>home stopped</td></tr>
<tr><td>late_check</td><td>{sec['late_check']:.1f}</td><td>home serving again</td></tr>
<tr><td>retention</td><td>{sec['retention']:.1f}</td><td>home serving again</td></tr></table>
<p>The record reached <code>backup</code> and then <code>snapshot_verified</code>. Retention kept three snapshots. The copy wrote little between staging and the stop, so live's catch-up can take longer than 3.5 s.</p>
<h2>Delivery</h2>
<p>One owner message was sent to each recipient through the web app's own owner routes on the copy. None was accepted. Each was refused at once, because no machine is joined to the copy.</p>
<table><tr><th>To</th><th>Name</th><th>Answer</th><th>Time</th><th>Outcome by ID after 60 s</th></tr>{drows}</table>
<p>The CLI route <code>POST /api/messages/send</code> gave the same 400 for the Chief. There, the outcome by ID is <code>null</code>, so home does not know the ID. Note: the stored refusal reason <code>write_to_backend_failed</code> does not name the real cause shown in the answer, "Waiting for VPS 1". The Ticket's outcome also carries <code>conversation_id: null</code>.</p>
<h2>Browser: {sum(r['pass'] for r in br['results'])} of {len(br['results'])} passed</h2>
<p>Each conversation was opened in headless Chromium on loopback. Chief and Manager panes start collapsed, so the checker pressed Open as the owner would. A pass needs the transcript region to reach <code>complete</code>, the tail stream to send a snapshot and stay open for 2 more seconds, no connection-trouble mark, and no "upgrading" 409 or text. Times run from navigation. The whole run took {br['total_seconds']:.0f} s. Managers: median {sorted(r['complete_seconds'] for r in mgr)[len(mgr)//2]:.2f} s, slowest {max(r['complete_seconds'] for r in mgr):.2f} s. The only API errors were the expected backend catalog 503 (Chief and Manager) and 409 (Ticket), because no machine is joined. A three-target trial had opened the Chief, one Manager and one Ticket once before this run.</p>
<table><tr><th>Kind</th><th>Conversation</th><th>Complete (s)</th><th>Stream snapshot (s)</th><th>Result</th></tr>{rows}</table>
<p>Screenshots in <code>browser/screenshots/</code> hide all message text and media. Manager titles and control labels stay readable.</p>
<h2>Stop, delete, and live</h2>
<p>The launcher ran under <code>setsid</code> as the exec'd process, and stop signals only that recorded PID. Both stops logged "Shutdown complete; code: 0". In the final stop, one SIGTERM stopped the supervisor, the application and two metrics readers in {fs['all_stopped_seconds']:.2f} s, and the port closed. In the earlier stop, the supervisor exited in {ss['supervisor_exit_seconds']:.2f} s. A bug in my stop script made that stop wait 90 s and report "not stopped"; see the log notes. The copy, its data and the release folders are deleted (18 GB).</p>
<p>Live after the rehearsal: {cmp_['skill_entries_after']} skill entries unchanged with checksums, command unchanged, routes unchanged, live units unchanged, health ready on 3d0c534. See <code>live-comparison.json</code>.</p>
<h2>How the copy was made safe</h2>
<p>An online SQLite backup through a read-only live connection took {prep['backup_seconds']:.1f} s; conversation files took {prep['conversation_files_seconds']:.1f} s. Before the start, the following were removed: {', '.join(f'{k} {v}' for k,v in prep['sanitized_rows'].items())}. The foreign-key check then found {prep['foreign_key_violations']} violations. The home ran with private HOME and provider folders, dispatch and notifications off, an inert update token, a private Worker unit prefix and no command link. The hosted build came from run 45874730337, archive sha256 <code>{adm['archive_sha256'][:16]}…</code>, which matches its build receipt. It was admitted in {adm['admission_seconds']:.1f} s. All scripts are in <code>scripts/</code>.</p>
"""
open(f"{A}/rehearsal-report.html", "w").write(page)
print(len(page))
