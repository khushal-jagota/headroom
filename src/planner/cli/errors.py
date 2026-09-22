"""Concise, truthful error output for the :mod:`planner.cli` command tree."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from typing import Any, NoReturn, TextIO

import click

LEGACY_SUPERVISOR_RECOVERIES: dict[str, str] = {
    "show": "panels sprint item show ITEM_ID",
    "context": "panels sprint item workspace ITEM_ID",
    "ticket-context": "panels ticket show TICKET_ID",
    "history": "panels ticket history TICKET_ID",
    "message-worker": "panels send-message --ticket TICKET_ID --message TEXT",
    "restart-worker": "panels ticket restart-worker TICKET_ID",
    "set-item": "panels sprint item set ITEM_ID FIELD --value VALUE",
    "set-ticket": "panels ticket edit TICKET_ID --input-json FILE",
    "artifact-list": "panels sprint item artifact list ITEM_ID",
    "artifact-write": (
        "panels sprint item artifact write ITEM_ID ARTIFACT_PATH --body-file FILE"
    ),
    "artifact-delete": "panels sprint item artifact delete ITEM_ID ARTIFACT_PATH",
    "send": "panels send-message --sprint-item ITEM_ID --message TEXT",
    "reset": "panels sprint item conversation reset ITEM_ID",
    "approve": "panels ticket proposal TICKET_ID accept --ceiling STAGE",
    "reject": "panels ticket proposal TICKET_ID revise < GUIDANCE_FILE",
}

# These commands enforce one input rule after Click parses their options. Their shortest
# usable forms are kept here so a validation error never points back to an incomplete
# ``[OPTIONS]`` synopsis.
COMMAND_RECOVERY_SHAPES: dict[str, str] = {
    "panels send-message": "panels send-message --owner --message TEXT",
    "panels schedule set": "panels schedule set SCHEDULE_ID FIELD --value VALUE",
    "panels worker-type save": "panels worker-type save < RECORD_FILE",
    "panels worker-type skill": (
        "panels worker-type skill WORKER_TYPE --description TEXT < SKILL_FILE"
    ),
    "panels project set": "panels project set PROJECT_ID FIELD --value VALUE",
    "panels day set": "panels day set FIELD --value TEXT",
    "panels ticket delete": "panels ticket delete TICKET_ID --yes",
    "panels ticket create": "panels ticket create --input-json FILE",
    "panels ticket edit": "panels ticket edit TICKET_ID --input-json FILE",
    "panels ticket proposal": "panels ticket proposal TICKET_ID ACTION",
    "panels ticket request-help": "panels ticket request-help TICKET_ID < MESSAGE_FILE",
    "panels ticket set": "panels ticket set TICKET_ID FIELD --value VALUE",
    "panels ticket complete": "panels ticket complete TICKET_ID FIELD --value VALUE",
    "panels ticket set-value": "panels ticket set-value TICKET_ID FIELD --value VALUE",
    "panels ticket place": "panels ticket place TICKET_ID --project-id PROJECT_ID",
    "panels ticket approve": "panels ticket approve TICKET_ID --ceiling STAGE",
    "panels ticket reject": "panels ticket reject TICKET_ID --message TEXT",
    "panels sprint set": "panels sprint set SPRINT_ID FIELD --value VALUE",
    "panels sprint item create": (
        "panels sprint item create --title TEXT --project-id PROJECT_ID"
    ),
    "panels sprint item delete": "panels sprint item delete ITEM_ID --yes",
    "panels sprint item set": "panels sprint item set ITEM_ID FIELD --value VALUE",
    "panels sprint item conversation send": (
        "panels sprint item conversation send ITEM_ID --message TEXT"
    ),
    "panels worker propose": "panels worker propose TICKET_ID < PROPOSAL_FILE",
    "panels worker request-help": "panels worker request-help TICKET_ID < MESSAGE_FILE",
    "panels worker recap": "panels worker recap TICKET_ID < RECAP_FILE",
    "panels worker note": "panels worker note TICKET_ID < GUIDANCE_FILE",
}

_PANELS_CALL = re.compile(r"(?:^|[`'\" ])panels(?:[ `]|$)")


def _one_line(message: str) -> str:
    return " ".join(part.strip() for part in message.splitlines() if part.strip())


def _canonical_command_path(ctx: click.Context) -> str:
    names: list[str] = []
    current: click.Context | None = ctx
    while current is not None:
        if current.info_name:
            names.append(current.info_name)
        current = current.parent
    names.reverse()
    if names:
        names[0] = "panels"
    return " ".join(names) if names else "panels"


def _option_shape(option: click.Option, ctx: click.Context) -> str:
    option_name = next(
        (name for name in option.opts if name.startswith("--")), option.opts[0]
    )
    if option.is_flag:
        return option_name
    return f"{option_name} {option.make_metavar(ctx)}"


def command_shape(ctx: click.Context) -> str:
    """Return the shortest complete parser shape for one registered command."""
    command = ctx.command
    command_path = _canonical_command_path(ctx)
    recovery = COMMAND_RECOVERY_SHAPES.get(command_path)
    if recovery is not None:
        return recovery
    parts = [command_path]
    options = [
        parameter
        for parameter in command.params
        if isinstance(parameter, click.Option) and not parameter.hidden
    ]
    parts.extend(_option_shape(option, ctx) for option in options if option.required)
    if any(not option.required for option in options):
        parts.append("[OPTIONS]")
    parts.extend(
        parameter.make_metavar(ctx)
        for parameter in command.params
        if isinstance(parameter, click.Argument)
    )
    return " ".join(parts)


def current_command_shape() -> str | None:
    ctx = click.get_current_context(silent=True)
    return None if ctx is None else command_shape(ctx)


def render_human_error(
    message: str,
    *,
    recovery: str | None = None,
    no_route: str | None = None,
    file: TextIO | None = None,
) -> None:
    """Write one cause line and, when needed, one truthful recovery line."""
    stream = file if file is not None else click.get_text_stream("stderr")
    one_line_message = _one_line(message)
    click.echo(f"error: {one_line_message}", file=stream)
    if no_route is not None:
        click.echo(_one_line(no_route), file=stream)
        return
    if recovery is None and not _PANELS_CALL.search(one_line_message):
        recovery = current_command_shape()
    if recovery is not None:
        click.echo(f"Use: {recovery}", file=stream)


def _context_for_child(ctx: click.Context, name: str) -> click.Context | None:
    command = ctx.command
    if not isinstance(command, click.Group):
        return None
    child = command.get_command(ctx, name)
    if child is None:
        return None
    return click.Context(child, info_name=name, parent=ctx)


def _legacy_supervisor_recovery(args: Sequence[str]) -> str | None:
    prefix = ("sprint", "item", "supervisor")
    if tuple(args[:3]) != prefix or len(args) < 4:
        return None
    return LEGACY_SUPERVISOR_RECOVERIES.get(args[3])


def _show_usage_error(error: click.UsageError, args: Sequence[str]) -> None:
    ctx = error.ctx
    if isinstance(error, click.exceptions.NoArgsIsHelpError):
        path = _canonical_command_path(ctx) if ctx is not None else "panels"
        render_human_error(
            f"{path} needs a command.",
            no_route="No panels call can be named until an action is selected.",
        )
        return

    if isinstance(error, click.NoSuchCommand):
        path = _canonical_command_path(ctx) if ctx is not None else "panels"
        recovery = _legacy_supervisor_recovery(args)
        if recovery is None and ctx is not None and error.possibilities:
            child_ctx = _context_for_child(ctx, error.possibilities[0])
            recovery = command_shape(child_ctx) if child_ctx is not None else None
        no_route = None
        if recovery is None:
            no_route = f'No replacement panels command can be inferred for "{error.command_name}".'
        render_human_error(
            f'"{error.command_name}" is not a command under {path}.',
            recovery=recovery,
            no_route=no_route,
        )
        return

    if isinstance(error, click.NoSuchOption):
        path = _canonical_command_path(ctx) if ctx is not None else "panels"
        if ctx is not None and isinstance(ctx.command, click.Group):
            render_human_error(
                f'"{error.option_name}" is not an option for {path}.',
                no_route="No panels call can be named until an action is selected.",
            )
            return
        render_human_error(
            f'"{error.option_name}" is not an option for {path}.',
            recovery=command_shape(ctx) if ctx is not None else None,
        )
        return

    render_human_error(
        error.format_message(),
        recovery=command_shape(ctx) if ctx is not None else None,
    )


def _resolved_context(root: click.Group, args: Sequence[str]) -> click.Context:
    ctx = click.Context(root, info_name="panels")
    command: click.Command = root
    for token in args:
        if not isinstance(command, click.Group) or token.startswith("-"):
            break
        child = command.get_command(ctx, token)
        if child is None:
            break
        ctx = click.Context(child, info_name=token, parent=ctx)
        command = child
    return ctx


def _show_click_exception(
    error: click.ClickException,
    root: click.Group,
    args: Sequence[str],
) -> None:
    ctx = _resolved_context(root, args)
    path = _canonical_command_path(ctx)
    message = error.format_message()
    if (
        path == "panels environment run"
        and message == "environment instance is not prepared"
    ):
        render_human_error(
            message,
            recovery=(
                "panels environment prepare --kind staging --environment-root DIRECTORY"
            ),
        )
        return
    if path in {"panels restart", "panels serve"}:
        render_human_error(
            message,
            no_route="No follow-up panels call is available for this process failure.",
        )
        return
    render_human_error(message, recovery=command_shape(ctx))


class PanelsGroup(click.Group):
    """Root group that replaces Click's usage walls with two-line errors."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        supplied_args = list(sys.argv[1:] if args is None else args)
        if not standalone_mode:
            return super().main(
                args=supplied_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        try:
            super().main(
                args=supplied_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except click.UsageError as error:
            _show_usage_error(error, supplied_args)
            raise SystemExit(error.exit_code) from error
        except click.ClickException as error:
            _show_click_exception(error, self, supplied_args)
            raise SystemExit(error.exit_code) from error
        except click.Abort as error:
            render_human_error(
                "The caller stopped the operation.",
                no_route="No follow-up panels call is required.",
            )
            raise SystemExit(1) from error
        raise SystemExit(0)


def exit_with_human_error(
    message: str,
    *,
    exit_code: int,
    recovery: str | None = None,
    no_route: str | None = None,
) -> NoReturn:
    render_human_error(message, recovery=recovery, no_route=no_route)
    raise SystemExit(exit_code)
