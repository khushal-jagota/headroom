"""Selected read routes share the existing public Ticket read authority."""

from fastapi import APIRouter

from planner.tickets.api import DbConn
from planner.tickets.workspace.contracts import WorkspaceFieldBody, WorkspaceTicket
from planner.tickets.workspace.views import workspace_field, workspace_ticket

router = APIRouter()


@router.get("/tickets/{ticket_id}/workspace")
def read_workspace_ticket(ticket_id: str, conn: DbConn) -> WorkspaceTicket:
    return workspace_ticket(conn, ticket_id)


@router.get("/tickets/{ticket_id}/workspace/fields/{field}")
def read_workspace_field(ticket_id: str, field: str, conn: DbConn) -> WorkspaceFieldBody:
    return workspace_field(conn, ticket_id, field)
