"""Protect explicit identity policy, including AsyncSSH's adjacent certificate trap."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest

from planner.executor import ssh
from planner.executor.contracts import ExecutorSshConfig


def test_client_options_disable_ambient_credentials_and_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    key = asyncssh.generate_private_key("ssh-ed25519")
    key.write_private_key(identity)
    ca = asyncssh.generate_private_key("ssh-ed25519")
    ca.generate_user_certificate(key, "unrequested-cert").write_certificate(
        str(identity) + "-cert.pub"
    )
    key.write_public_key(str(identity) + ".pub")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.touch()
    monkeypatch.setenv("SSH_AUTH_SOCK", "/ambient/agent")
    capture = AsyncMock()
    monkeypatch.setattr(asyncssh, "connect", capture)
    asyncio.run(
        ssh.connect_outer(
            ExecutorSshConfig("explicit.example", 2222, "explicit-user", identity, known_hosts)
        )
    )
    options = capture.call_args.kwargs["options"]
    assert isinstance(options, asyncssh.SSHClientConnectionOptions)
    assert capture.call_args.kwargs["config"] is None
    assert capture.call_args.kwargs["known_hosts"] == str(known_hosts)
    assert options.client_keys is not None
    assert len(options.client_keys) == 1
    assert not options.client_keys[0].has_cert
    assert options.client_keys[0].key_public_data == key.public_data
    assert options.agent_path == ""
    assert options.agent_identities is None
    assert options.agent_forward_path is None
    assert options.pkcs11_provider is None
    assert options.client_host_keysign is None
    assert not options.client_host_keypairs
    assert options.preferred_auth == ["publickey"]
    assert options.public_key_auth
    assert options.disable_trivial_auth
    assert not options.password_auth
    assert not options.kbdint_auth
    assert not options.host_based_auth
    assert options.password is None
    assert not options.gss_auth
    assert not options.gss_kex
    assert not options.gss_delegate_creds
    assert options.proxy_command is None
    assert options.tunnel is None
    assert not options.canonicalize_hostname
    assert options.host_key_alias is None
    assert options.x509_trusted_certs is None
    assert not options.x509_trusted_cert_paths
    assert not options.x11_forwarding
    assert not options.request_pty
    assert not options.env
    assert not options.send_env
    assert options.connect_timeout == 10
    assert options.login_timeout == 10
    assert options.keepalive_interval == 15
    assert options.keepalive_count_max == 3


def test_encrypted_identity_refuses_without_ambient_passphrase(tmp_path: Path) -> None:
    identity = tmp_path / "identity"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(
        identity, format_name="pkcs8-pem", passphrase="secret"
    )
    known_hosts = tmp_path / "known_hosts"
    known_hosts.touch()
    with pytest.raises(asyncssh.KeyImportError):
        asyncio.run(
            ssh.connect_outer(
                ExecutorSshConfig("explicit.example", 22, "user", identity, known_hosts)
            )
        )


def test_close_deadline_aborts_remaining_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = AsyncMock(spec=asyncssh.SSHClientConnection)
    connection.close = Mock()
    connection.abort = Mock()
    connection.wait_closed.side_effect = TimeoutError
    asyncio.run(ssh.close_connection(connection))
    connection.close.assert_called_once()
    connection.abort.assert_called_once()
