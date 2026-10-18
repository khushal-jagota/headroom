"""A private OpenSSH daemon. Never reads or changes the installed configuration."""

from __future__ import annotations

import contextlib
import os
import pwd
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import asyncssh

from planner.executor.contracts import ExecutorSshConfig


@contextlib.contextmanager
def isolated_sshd(directory: Path) -> Iterator[ExecutorSshConfig]:
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = directory / "identity"
    host_key = directory / "host"
    key = asyncssh.generate_private_key("ssh-ed25519")
    key.write_private_key(identity)
    identity.chmod(0o600)
    key.write_public_key(directory / "authorized_keys")
    server_key = asyncssh.generate_private_key("ssh-ed25519")
    server_key.write_private_key(host_key)
    host_key.chmod(0o600)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    username = pwd.getpwuid(os.getuid()).pw_name
    config = directory / "sshd_config"
    config.write_text(
        "\n".join(
            [
                f"Port {port}",
                "ListenAddress 127.0.0.1",
                f"HostKey {host_key}",
                f"PidFile {directory / 'pid'}",
                f"AuthorizedKeysFile {directory / 'authorized_keys'}",
                "StrictModes no",
                "UsePAM no",
                "PasswordAuthentication no",
                "KbdInteractiveAuthentication no",
                "PubkeyAuthentication yes",
                "AllowStreamLocalForwarding yes",
                "AllowTcpForwarding yes",
                "StreamLocalBindUnlink no",
                "LogLevel DEBUG1",
            ]
        )
        + "\n"
    )
    known_hosts = directory / "known_hosts"
    known_hosts.write_bytes(f"[127.0.0.1]:{port} ".encode() + server_key.export_public_key())
    with (directory / "sshd.log").open("wb") as log:
        process = subprocess.Popen([sshd, "-D", "-e", "-f", str(config)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    raise RuntimeError((directory / "sshd.log").read_text())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("private sshd did not listen") from None
                    time.sleep(0.02)
            yield ExecutorSshConfig("127.0.0.1", port, username, identity, known_hosts)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
