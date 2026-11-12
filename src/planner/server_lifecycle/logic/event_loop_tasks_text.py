"""The human-readable form of `panels server-event-loop-tasks`."""

from planner.server_lifecycle.contracts import EventLoopTask, EventLoopTasksResponse


def render_event_loop_tasks(response: EventLoopTasksResponse) -> str:
    lines = [
        f"{response.total_task_count} asyncio tasks on the Panels server event loop, oldest first."
    ]
    for task in response.tasks:
        lines.append("")
        lines.append(f"{task.name}  {task.coroutine}  {_age_text(task)}")
        for frame in task.stack:
            line = "?" if frame.line is None else str(frame.line)
            lines.append(f"    {frame.file}:{line} in {frame.function}")
    if response.omitted_task_count:
        lines.append("")
        lines.append(
            f"{response.omitted_task_count} more tasks are not shown: "
            "the answer reached its size limit."
        )
    return "\n".join(lines)


def _age_text(task: EventLoopTask) -> str:
    if task.age_seconds is None:
        return "age unknown"
    return f"age {format_task_age(task.age_seconds)}"


def format_task_age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"
