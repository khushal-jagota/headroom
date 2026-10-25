"""Protect explicit identity policy, including AsyncSSH's adjacent certificate trap."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest

from planner.executor import ssh
from planner.executor.contracts import ExecutorSshConfig, SshAuthenticationMode


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
            ExecutorSshConfig(
                "explicit.example",
                2222,
                "explicit-user",
                identity,
                known_hosts,
                SshAuthenticationMode.public_key,
            )
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
                ExecutorSshConfig(
                    "explicit.example",
                    22,
                    "user",
                    identity,
                    known_hosts,
                    SshAuthenticationMode.public_key,
                )
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


def test_tailscale_suppresses_key_loaders_and_preserves_original_host_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor.contracts import ValidatedTailnetDestination

    known_hosts = tmp_path / "known_hosts"
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    revoked_key = asyncssh.generate_private_key("ssh-ed25519")
    known_hosts.write_bytes(
        b"peer.example "
        + host_key.export_public_key()
        + b"@revoked peer.example "
        + revoked_key.export_public_key()
    )
    binary = tmp_path / "tailscale"
    binary.touch(mode=0o700)
    capture = AsyncMock()
    validation = AsyncMock(return_value=ValidatedTailnetDestination("node", "100.64.1.2"))
    monkeypatch.setattr(ssh, "validate_tailnet_destination", validation)
    monkeypatch.setattr(asyncssh, "connect", capture)
    import asyncssh.connection as connection

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("ambient key loader was consulted")

    monkeypatch.setattr(connection, "load_default_keypairs", forbidden)
    monkeypatch.setattr(asyncssh, "import_private_key", forbidden)
    monkeypatch.setenv("SSH_AUTH_SOCK", "/ambient/agent")
    asyncio.run(
        ssh.connect_outer(
            ExecutorSshConfig(
                "peer.example",
                22,
                "user",
                None,
                known_hosts,
                SshAuthenticationMode.tailscale,
                binary,
            )
        )
    )
    validation.assert_awaited_once_with("peer.example", binary)
    assert capture.call_args.args == ("100.64.1.2", 22)
    kwargs = capture.call_args.kwargs
    assert kwargs["host_key_alias"] == "peer.example"
    assert kwargs["known_hosts"] == asyncssh.read_known_hosts(str(known_hosts)).match(
        "peer.example", "", None
    )
    assert kwargs["known_hosts"][2] == [revoked_key.convert_to_public()]
    options = kwargs["options"]
    assert options.client_keys is None
    assert not options.public_key_auth
    assert not options.disable_trivial_auth
    assert not options.password_auth and not options.kbdint_auth
    assert not options.host_based_auth and not options.gss_auth and not options.gss_kex
    assert options.agent_path is None
    assert options.proxy_command is None and options.tunnel is None
    assert not options.request_pty and not options.env and not options.send_env
    # The generation-scoped SFTP transport retains key-only trivial-auth refusal.
    inner = ssh._client_options(host_key, "generation")
    assert inner.public_key_auth and inner.disable_trivial_auth
    assert inner.preferred_auth == ["publickey"]


@pytest.mark.parametrize(
    ("mode", "identity", "binary", "port"),
    [
        (SshAuthenticationMode.public_key, None, None, 22),
        (SshAuthenticationMode.public_key, Path("/key"), Path("/tailscale"), 22),
        (SshAuthenticationMode.tailscale, Path("/key"), Path("/tailscale"), 22),
        (SshAuthenticationMode.tailscale, None, None, 22),
        (SshAuthenticationMode.tailscale, None, Path("/tailscale"), 2222),
        ("public_key", Path("/key"), None, 22),
    ],
)
def test_authentication_inputs_refuse_contradictions(
    mode: SshAuthenticationMode,
    identity: Path | None,
    binary: Path | None,
    port: int,
) -> None:
    from planner.executor.contracts import SshConfigurationError
    from planner.executor.logic.ssh_configuration import validate_authentication_inputs

    with pytest.raises(SshConfigurationError):
        validate_authentication_inputs(mode, identity, binary, port)
