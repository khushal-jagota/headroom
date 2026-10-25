"""Explicit outer authentication inputs, without filesystem or network access."""

from pathlib import Path

from planner.executor.contracts import SshAuthenticationMode, SshConfigurationError


def validate_authentication_inputs(
    mode: SshAuthenticationMode,
    identity_file: Path | None,
    tailscale_binary: Path | None,
    port: int,
) -> None:
    if not isinstance(mode, SshAuthenticationMode):
        raise SshConfigurationError("SSH authentication mode must be explicit")
    if mode is SshAuthenticationMode.public_key:
        if identity_file is None or tailscale_binary is not None:
            raise SshConfigurationError("Public-key mode requires only an explicit identity file")
    elif identity_file is not None or port != 22 or tailscale_binary is None:
        raise SshConfigurationError(
            "Tailscale mode requires port 22 and an explicit binary, no key"
        )
