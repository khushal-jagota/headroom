"""Named execution destinations, independent of local physical workspace UUIDs."""

from dataclasses import dataclass
from typing import Literal, TypedDict

HOME_MACHINE_ID = "75a49e78-97de-4e28-8dc5-79646761c46f"
ORIGINAL_MAC_MACHINE_ID = "ee17b608-a449-4966-a808-a791ba7df1c7"
MachineIcon = Literal["server", "laptop", "desktop", "cloud"]
MachineColor = Literal["jade", "orchid", "sand", "cyan", "rose", "plum", "lime", "stone"]


@dataclass(frozen=True, slots=True)
class Machine:
    machine_id: str
    name: str
    icon: MachineIcon
    color: MachineColor
    is_home: bool


class MachineView(TypedDict):
    machine_id: str
    name: str
    icon: MachineIcon
    color: MachineColor
    is_home: bool
    available: bool
    running_workers: int | None
    waiting_tickets: int
