# Install an extra Linux machine

A Linux executor runs Workers for one home. It holds private credentials, workspaces, journals, and output, but no home database.

The archive supports the same Linux x86_64 hosts as the Linux home: Debian 13 and Ubuntu 24.04 with the declared processor features. Host Node 22.18.0 or newer, `python3` for the join script, an authenticated GitHub CLI, and a systemd user manager are prerequisites. Provider sign-ins belong to this Linux account.

Each release publication builds the archive in `publish-typescript-release.yml` with `server/environments/linux_executor/build_main.ts`. The publication holds `panels-linux-executor.tar.gz` and `linux-executor-publication.json`. The receipt records the exact revision, digest, size, and executor target. The archive uses the host Node. It holds no private Node.

On the home, register the machine and print its join command:

```sh
panels machine join-command "Linux server" --home https://HOME_ADDRESS
```

Settings → Machines issues the same command. The home must run a released build. The output holds the command, the private credential, and a cleanup step. The executor reports its platform when it authenticates, so registration needs no platform choice.

On the Linux machine, run the printed command. It downloads `scripts/panels_install.py` at the home's revision and runs it with `python3`. Paste the credential at the private prompt. Keep the credential out of command arguments and shell history. Then remove the temporary script as the output says.

The join script checks the host, the archive, and the release target before it reads the credential. It installs the runtime under `${XDG_DATA_HOME:-$HOME/.local/share}/panels/executor-typescript`. The `data/` folder holds the credential and executor state. The `installation/releases/` folder holds versioned runtimes. It installs and enables `panels-executor-typescript.service`, then waits for the executor to reach the home. A failed attempt removes only its own files.

For service startup after reboot without an account login, an administrator must enable linger:

```sh
sudo loginctl enable-linger ACCOUNT_NAME
```

Check or stop the service with these commands:

```sh
systemctl --user status panels-executor-typescript.service
journalctl --user -u panels-executor-typescript.service
systemctl --user stop panels-executor-typescript.service
```

The executor uses shared Worker supervision for process ownership and bounded shutdown. Systemd gives the supervisor time to stop its children. Unproved cleanup retains the existing fences.

Do not join an initialized executor again. The join refuses existing data, releases, or service state.

Updates retain the data folder. The home offers the Linux executor archive for this destination. A Linux home archive or Mac executor archive cannot replace this executor.

After a home restart, Linux update targets can remain unavailable for about 20 seconds. Archive checks run in the background. A failed archive check blocks repeat downloads of that digest until a different release appears or the home restarts.

For credential replacement, run `panels machine replace-credential MACHINE_ID` on the home. Transfer the new credential privately as a JSON file with a `credential` field and mode `0600`. Stop this service. Read the runtime's Node and entrypoint from the `ExecStart` line of `systemctl --user cat panels-executor-typescript.service`, then install the credential into the same data folder:

```sh
PLAN_EXECUTOR_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/panels/executor-typescript/data" \
  /path/to/node /path/to/runtime/server/executor/mac/entrypoint.ts \
  executor credential --file /path/to/replacement-credential.json
systemctl --user start panels-executor-typescript.service
```

Keep disposable checks separate from live home data and executor settings.
