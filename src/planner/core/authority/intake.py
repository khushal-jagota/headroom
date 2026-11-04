"""Rank admission at HTTP and executor intake. Internal calls act as Panels."""

from __future__ import annotations

import json
import re
import sqlite3
from email.message import Message
from collections.abc import Mapping
from contextlib import closing
from typing import Any
from urllib.parse import unquote

from fastapi import Request
from starlette._utils import get_route_path

from planner.core import authority
from planner.core.authctx import request_context
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError


def admit_message(conn: sqlite3.Connection, caller: Principal, recipient: Principal) -> None:
    from planner.managers import data
    from planner.message_delivery.logic.permissions import permits_manager_message

    if permits_manager_message(caller, recipient):
        data.read_manager(conn, caller.id)
        data.read_manager(conn, recipient.id)
        return
    if recipient.kind in (PrincipalKind.owner, PrincipalKind.chief):
        return
    target = authority.Target(authority.TargetKind(recipient.kind.value), recipient.id)
    if authority.is_above(conn, caller, target):
        return
    if caller.kind not in (PrincipalKind.owner, PrincipalKind.chief):
        own = authority.Target(authority.TargetKind(caller.kind.value), caller.id)
        if authority.is_above(conn, recipient, own):
            return
    authority.require_above(conn, caller, target)


def _admit_source_assignment(
    conn: sqlite3.Connection, principal: Principal, responsible: Principal
) -> None:
    if responsible.kind is PrincipalKind.goal:
        authority.require_above_or_self(conn, principal, authority.goal(responsible.id))
        return
    if responsible.kind is PrincipalKind.manager:
        authority.require_in_chain(conn, principal, authority.manager(responsible.id))
        return
    if principal.kind is PrincipalKind.ticket:
        from planner.tickets import data as tickets_data

        ticket = tickets_data.read_ticket(conn, principal.id)
        if ticket.manager_kind == "chief":
            return
        raise PlannerError(ErrorCode.agent_forbidden, "source belongs to another assignment")
    authority.require_above(conn, principal, authority.owner_only("measure_sources"))


def admit_command(
    conn: sqlite3.Connection,
    principal: Principal,
    method: str,
    route: str,
    path_params: Mapping[str, Any],
    body: Mapping[str, Any],
) -> None:
    """Use the matched route and its object, never an endpoint-name policy registry."""
    from planner.message_delivery.api import _principal
    from planner.tickets.logic.admission import TICKET_FIELDS_ONLY_FROM_ABOVE

    route = route.replace('/api/items', '/api/managers', 1)
    parts = route.strip('/').split('/')
    family = parts[1] if parts[0] == 'api' and len(parts) > 1 else parts[0]
    writing = method not in ('GET', 'HEAD', 'OPTIONS')
    target = None
    own = False
    if family == 'messages' and writing:
        admit_message(conn, principal, _principal(body.get('target')))
        return
    if family == 'feedback' and writing:
        if route.endswith('/use'):
            from planner.tickets.api import body_str

            target, own = authority.ticket(body_str(dict(body), 'ticket_id')), True
        else:
            target = authority.owner_only('feedback')
    elif family == 'tickets':
        from planner.managers.assignment import normalize_assignment

        body = normalize_assignment(dict(body))
        ticket_id = path_params.get('ticket_id')
        if ticket_id is None:
            if method == 'POST':
                raw_manager = body.get('manager')
                manager_id = None
                if raw_manager is not None:
                    manager = _principal(raw_manager)
                    if manager.kind is PrincipalKind.manager:
                        manager_id = manager.id
                        authority.require_in_chain(conn, principal, authority.manager(manager_id))
                if body.get('ceiling') is not None or body.get('ceiling_holder') is not None:
                    authority.require_above_a_ticket_being_created(
                        conn, principal, ticket_id='new-ticket', parent_manager_id=manager_id
                    )
            return
        if not writing:
            return
        if route.endswith('/propose'):
            return  # The proposal writer retains its self identity check.
        if route.endswith('/request-help'):
            authority.require_self(conn, principal, authority.ticket(ticket_id))
            if body.get('recipient') is not None:
                admit_message(conn, principal, _principal(body['recipient']))
            return
        target = authority.ticket(ticket_id)
        own = method == 'PATCH' and not (set(body) & (TICKET_FIELDS_ONLY_FROM_ABOVE | {'project'}))
        if method == 'PATCH' and set(body) & {'manager', 'manager_id'}:
            authority.refuse_manager_re_parenting(principal, ticket_id)
    elif family == 'collections':
        collection = path_params['collection']
        if collection in ('manager_tickets', 'outcome_tickets'):
            target = authority.ticket(path_params['member_id'])
            authority.refuse_manager_re_parenting(principal, target.id)
        elif collection == 'blockers':
            target, own = authority.ticket(path_params['container_id']), True
    elif family in ('managers', 'goals'):
        identifier = path_params.get('item_id' if family == 'managers' else 'goal_id')
        if identifier is not None:
            if route.endswith('/settled'):
                return  # Owner identity, rather than rank, governs settlement.
            protected_read = route.endswith((
                '/launch-defaults', '/workspace', '/workspace-content', '/conversation/start-values'
            ))
            if writing or protected_read:
                target = (authority.manager(identifier) if family == 'managers'
                          else authority.goal(identifier))
                own = method in ('GET', 'PATCH')
        elif family == 'goals' and writing:
            target = authority.owner_only('goals')
    elif family in ('artifact-catalog', 'conversation-history', 'files'):
        kind = path_params.get('kind')
        if kind == 'sprint_item' or family == 'conversation-history' and kind == 'orchestrator':
            kind = 'manager'
        if family == 'files':
            kind = ('manager' if 'manager_id' in path_params
                    else 'goal' if 'goal_id' in path_params else 'ticket')
        identifier = path_params.get(
            'owner_id', path_params.get('manager_id', path_params.get('goal_id'))
        )
        if kind in ('manager', 'goal') and identifier:
            target, own = authority.Target(authority.TargetKind(kind), identifier), True
    elif family == 'conversation' and route.endswith('/backends') and body.get('ticket_id'):
        target, own = authority.ticket(body['ticket_id']), True
    elif family == 'chief' and writing:
        target = authority.owner_only('chief conversation')
    elif family in ('projects', 'skills', 'workers', 'worker-types') and writing:
        target = authority.owner_only(
            'worker_settings' if family == 'workers'
            else 'worker_types' if family == 'worker-types' else family
        )
    elif family == 'credentials' and method == 'DELETE':
        target = authority.owner_only('credentials')
    elif (family in ('measure-sources', 'measures', 'datasets')
          and writing and not route.endswith('/read')):
        from planner.measure_sources import data as sources
        from planner.measures import data as measures
        from planner.datasets import data as datasets

        source_id = path_params.get('source_id')
        if family == 'measure-sources':
            if route.endswith('/responsible'):
                target = authority.owner_only('measure_sources')
            elif source_id is None:
                from planner.measure_sources.api import principal as source_principal

                _admit_source_assignment(
                    conn, principal, source_principal(body.get('responsible'))
                )
                return
        else:
            identifier = path_params.get('measure_id' if family == 'measures' else 'dataset_id')
            if route.endswith('/manual-writes'):
                if principal.kind is PrincipalKind.goal:
                    return  # The writer checks the goal's bound manual measure.
                target = authority.owner_only('measures')
            elif identifier is not None:
                record = (measures.read_measure(conn, identifier) if family == 'measures'
                          else datasets.read_dataset(conn, identifier))
                source_id = record.source_id
            else:
                source_id = body.get('source_id')
            if source_id is None and target is None:
                target = authority.owner_only('measures')
        if target is None and source_id is not None:
            _admit_source_assignment(
                conn, principal, sources.read_source(conn, source_id).responsible
            )
            return
    if target is not None:
        (authority.require_above_or_self if own else authority.require_above)(conn, principal, target)


async def request_admission(request: Request) -> None:
    """FastAPI runs this once after route matching and before the command handler."""
    # Included routers expose their original local APIRoute in scope["route"].
    # Use the same complete path that routing matches, with its inclusion prefixes.
    route = get_route_path(request.scope)
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        protected = (
            route in ('/api/resource-body', '/api/resource-versions')
            or ('/managers/' in route or '/items/' in route or '/goals/' in route)
            and route.endswith((
                '/launch-defaults', '/workspace', '/workspace-content', '/conversation/start-values'
            ))
            or route.startswith('/files/')
            and ('manager_id' in request.path_params or 'goal_id' in request.path_params)
            or request.path_params.get('kind') in ('manager', 'goal', 'sprint_item')
            or '/conversation-history/' in route
            and request.path_params.get('kind') == 'orchestrator'
        )
        if not protected:
            return
    if route in ('/api/measures/read', '/api/datasets/read'):
        return
    content_type = request.headers.get('content-type')
    media = Message()
    media['content-type'] = content_type or ''
    accepts_json = not content_type or (
        media.get_content_maintype() == 'application'
        and (media.get_content_subtype() == 'json' or media.get_content_subtype().endswith('+json'))
    )
    body = {}
    if accepts_json and not route.startswith('/files/') and await request.body():
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return  # FastAPI retains its declared-body validation response.

    if not isinstance(body, dict):
        body = {}
    with closing(request.app.state.conn_factory()) as conn:
        principal = request_context(request).principal
        if route in ('/api/resource-body', '/api/resource-versions'):
            try:
                keys = ([request.query_params.get('key', '')] if route.endswith('resource-body')
                        else json.loads(request.query_params.get('keys', '[]')))
            except json.JSONDecodeError:
                return  # The resource handler supplies its existing validation response.
            if isinstance(keys, list):
                for key in keys:
                    match = (re.fullmatch(
                        r'/api/(?:managers|items)/([^/?]+)/workspace-content', key
                    ) if isinstance(key, str) else None)
                    if match:
                        authority.require_above_or_self(
                            conn, principal, authority.manager(unquote(match[1]))
                        )
            return
        admit_command(conn, principal, request.method, route, request.path_params, body)
