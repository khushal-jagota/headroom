from sqlite3 import Connection

from tests.support.principals import OWNER_PRINCIPAL

from planner.tickets import data


def _ticket(conn: Connection) -> str:
    return data.create_ticket(
        conn, title="Unlinked artifacts", worker_type="coding", principal=OWNER_PRINCIPAL,
        now=1, title_max_chars=200,
    ).id
