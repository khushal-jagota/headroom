# Install an extra Linux machine

A Linux executor runs Workers for one home. It holds private credentials, workspaces, journals, and output, but no home database.

The archive supports the same Linux x86_64 hosts as the Linux home: Debian 13 and Ubuntu 24.04 with the declared processor features. Node 22 or newer and a systemd user manager are prerequisites. Provider sign-ins belong to this Linux account.

Build the archive on Linux with the existing native packaging tools and upstream CPython 3.12, as CI uses through `actions/setup-python`. The existing packager does not support the patched interpreter from Debian or Ubuntu packages.

```sh
python scripts/build_linux_executor.py --source /path/to/checkout --revision FULL_GIT_SHA --output /path/to/new-output
```

The output contains `panels-linux-executor.tar.gz` and `linux-executor-publication.json`. The archive includes its interpreter and native dependencies. The receipt records its exact revision, digest, size, and executor target. The builder uses the Linux home's native audit and archive tools. It does not install a service.

On the home, register a destination:

```sh
panels machine register "Linux server" --json
```

The executor reports its platform when it authenticates. No platform choice is required at registration.

Transfer the returned credential privately to the Linux account as a JSON file with a `credential` field. Protect that file with mode `0600`. Keep credentials out of command arguments and shell history.

The archive contains the installer and its interpreter. Check the archive against the approved digest before extraction:

```sh
printf '%s  %s\n' ARCHIVE_SHA256 /path/to/panels-linux-executor.tar.gz | sha256sum --check
mkdir /path/to/installer
tar -xzf /path/to/panels-linux-executor.tar.gz -C /path/to/installer
```

Use the extracted interpreter to run the installer. Supply values from the publication receipt:

```sh
/path/to/installer/.venv/bin/python -I -B -m planner.environments.linux_executor \
  --installation "$HOME/.local/share/panels-executor-runtime" \
  --data "$HOME/.local/share/panels-executor-data" \
  --executor-id MACHINE_ID --server-url https://HOME_ADDRESS \
  --revision FULL_GIT_SHA --archive /path/to/panels-linux-executor.tar.gz \
  --archive-sha256 ARCHIVE_SHA256 --archive-size ARCHIVE_BYTES \
  --credential-file /path/to/private-credential.json
```

The installer verifies the archive, target, and host. It writes the credential into the private data folder. It installs `panels-executor.service`, reloads the user manager, and enables that service. The installation folder and data folder must be separate.

For service startup after reboot without an account login, an administrator must enable linger:

```sh
sudo loginctl enable-linger ACCOUNT_NAME
```

Check or stop the service with these commands:

```sh
systemctl --user status panels-executor.service
journalctl --user -u panels-executor.service
systemctl --user stop panels-executor.service
```

The executor uses shared Worker supervision for process ownership and bounded shutdown. Systemd gives the supervisor time to stop its children. Unproved cleanup retains the existing fences.

Do not reinstall an initialized executor. Use the update path to retain its selected runtime and data. If the first installer call fails, it removes only its own files so that a retry can proceed.

Updates retain the data folder. The home offers the Linux executor archive for this destination. The executor verifies it, quiesces work, and activates a versioned runtime through its fixed supervisor. Failed readiness restores the previous runtime. A Linux home archive or Mac executor archive cannot replace this executor.

After a home restart, Linux update targets can remain unavailable for about 20 seconds. Archive checks run in the background. A failed archive check blocks repeat downloads of that digest until a different release appears or the home restarts.

For credential replacement, use the home's machine credential command. Transfer the new credential privately. Stop this service, then use its installed interpreter and the same data folder:

```sh
PLAN_EXECUTOR_ROOT="$HOME/.local/share/panels-executor-data" \
  /path/to/installed/runtime/.venv/bin/python -m planner.executor.entrypoint \
  executor credential --file /path/to/replacement-credential.json
systemctl --user start panels-executor.service
```

Keep disposable checks separate from live home data and executor settings.
