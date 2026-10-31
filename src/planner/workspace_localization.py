"""Directory localization shared by native execution and metadata discovery."""

from pathlib import Path, PurePosixPath


def localize_workspace(
    workspace_folder: Path,
    *,
    workspace_home: Path,
    workspace_home_relative: str | None = None,
) -> Path:
    """Resolve the exact existing directory without a cwd or checkout fallback."""
    if workspace_home_relative is not None:
        relative = workspace_home_relative
        if (
            not isinstance(relative, str)
            or "\\" in relative
            or (relative and any(part in ("", ".", "..") for part in relative.split("/")))
            or PurePosixPath(relative).is_absolute()
        ):
            raise ValueError("unsafe home-relative workspace")
        home = workspace_home.resolve(strict=True)
        workspace = (home / relative).resolve(strict=True)
        workspace.relative_to(home)
    else:
        if not workspace_folder.is_absolute():
            raise ValueError("workspace needs absolute native path or home mapping")
        workspace = workspace_folder.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("mapped workspace is not a directory")
    return workspace
