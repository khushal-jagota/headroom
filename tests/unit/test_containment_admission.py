"""Pure evidence checks reject claims that the kernel boundary does not prove."""

from pathlib import Path

import pytest
from test_containment.admission import check_mounts, enclosing_mount, parse_mountinfo
from test_containment.contracts import (
    BackingMount,
    ContainmentContract,
    ContainmentRefusal,
)


def _mounts(extra: str = "") -> str:
    return "1 0 0:1 / / ro - tmpfs tmpfs ro\n2 1 0:12 / /scratch rw - tmpfs tmpfs rw\n" + extra


def _contract(*protected: str) -> ContainmentContract:
    return ContainmentContract(
        Path("/scratch"),
        BackingMount("0:12", "/"),
        tuple(Path(item) for item in protected),
        (),
    )


def test_forged_scratch_claim_cannot_authorize_host_backed_writes() -> None:
    mounts = parse_mountinfo(
        _mounts().replace(
            "0:12 / /scratch rw - tmpfs tmpfs", "8:1 /home/account /scratch rw - ext4 /dev/root"
        )
    )
    with pytest.raises(ContainmentRefusal, match="private tmpfs"):
        check_mounts(_contract(), mounts)


def test_unlisted_writable_host_bind_refuses() -> None:
    mounts = parse_mountinfo(_mounts("3 1 8:1 /home/account /alias rw - ext4 /dev/root rw\n"))
    with pytest.raises(ContainmentRefusal, match="unapproved writable mount"):
        check_mounts(_contract(), mounts)


def test_writable_protected_entry_refuses_even_on_private_tmpfs() -> None:
    with pytest.raises(ContainmentRefusal, match="protected path remains writable"):
        check_mounts(_contract("/scratch/skills"), parse_mountinfo(_mounts()))


def test_readonly_projection_protects_entry_from_writable_parent() -> None:
    mounts = parse_mountinfo(_mounts("3 2 8:1 /protected /scratch/skills ro - ext4 /dev/root ro\n"))
    check_mounts(_contract("/scratch/skills"), mounts)


def test_stacked_mount_selection_and_escaped_paths() -> None:
    mounts = parse_mountinfo(
        "1 0 0:1 / / ro - tmpfs tmpfs ro\n"
        "2 1 8:1 /old /space\\040here rw - ext4 /dev/root rw\n"
        "3 2 0:12 / /space\\040here ro - tmpfs tmpfs ro\n"
    )
    assert enclosing_mount(Path("/space here/child"), mounts).mount_id == 3


def test_ambiguous_stacked_mounts_refuse() -> None:
    mounts = parse_mountinfo(_mounts("3 1 8:1 /other /scratch ro - ext4 /dev/root ro\n"))
    with pytest.raises(ContainmentRefusal, match="ambiguous"):
        enclosing_mount(Path("/scratch"), mounts)


def test_forged_provider_path_cannot_authorize_an_arbitrary_host_bind() -> None:
    from dataclasses import replace

    contract = replace(
        _contract(),
        provider_profile_paths=(Path("/some-account"),),
        provider_profile_mounts=((Path("/some-account"), BackingMount("8:1", "/some-account")),),
    )
    mounts = parse_mountinfo(
        _mounts("3 1 8:1 /some-account /some-account rw - ext4 /dev/root rw\n")
    )
    with pytest.raises(ContainmentRefusal, match="canonical account profile"):
        check_mounts(contract, mounts)


def test_omitted_protected_inventory_does_not_hide_account_defaults() -> None:
    import os
    import pwd

    account = pwd.getpwuid(os.getuid()).pw_dir
    mounts = parse_mountinfo(_mounts(f"3 1 0:44 / {account}/.codex rw - tmpfs tmpfs rw\n"))
    with pytest.raises(ContainmentRefusal, match="protected path remains writable"):
        check_mounts(_contract(), mounts)
