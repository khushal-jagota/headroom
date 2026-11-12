"""Regenerate Python answers and compare actual TS functions on SQLite copies.

Run after every staging merge from the Ticket worktree:
  scripts/dev pytest -q -s server/tests/worker_runtime_parity.py
Requires that worktree's isolated Python and server dependencies. No saved oracle.
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Mapping, Callable
from typing import Any
from enum import Enum
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch

from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.support.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.db import connect, create_schema
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.clock import TestClock as RuntimeTestClock
from planner.core.errors import PlannerError
from planner.tickets import data as tickets
from planner.runtime import conversation_start as start, worker_step_readiness as readiness
from planner.runtime.workspaces import register_workspace_root
from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.message_content import text_message_content
from planner.managers import data as managers
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.configuration import MAC_AWAY_AFTER_SECONDS
from planner.tickets.contracts import Ticket
from planner.runtime.logic.conversation_start_resolution import ConversationStartValues
from planner.conversation.contracts import PromptDeliveryFate

ROOT = Path(__file__).resolve().parents[2]

NOW = 1783159200
WALL = NOW * 1000
TABLES = ('tickets', 'ticket_paired_stage_openers', 'ticket_conversations', 'agents', 'conversations')

def plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name:plain(getattr(value,field.name)) for field in dataclasses.fields(value)}
    if isinstance(value,Mapping):return {key:plain(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [plain(item) for item in value]
    if isinstance(value,Enum):return value.value
    if isinstance(value,Path):return str(value)
    return value

def rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1,2')] for table in TABLES}

def new_ticket(conn: sqlite3.Connection, worker_type: str='coding') -> Ticket:
    return tickets.create_ticket(conn, title='Parity Ticket', worker_type=worker_type,
        brief='Agreed brief', principal=OWNER_PRINCIPAL, now=NOW, title_max_chars=200,
        stated_ceiling='needs_what_changes' if worker_type=='coding' else 'needs_stages',
        stated_holder=OWNER_PRINCIPAL)

def compare(conn: sqlite3.Connection, root: Path, spec: dict[str, Any], python: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    ts_path = root/'typescript.sqlite'
    with sqlite3.connect(ts_path) as target:
        conn.backup(target)
    spec.update(db_path=str(ts_path), workspace=str(root), now=NOW, wall=WALL)
    input_path=root/'input.json'
    input_path.write_text(json.dumps(spec,default=str))
    expected=python()
    actual=json.loads(subprocess.check_output(['node',str(ROOT/'server/tests/worker_runtime_parity.ts'),str(input_path)],cwd=ROOT,text=True))
    if actual!=expected:
        (root/'difference.json').write_text(json.dumps({'python':expected,'typescript':actual},indent=2,default=str))
        def differences(a: Any,b: Any,path: str='') -> list[tuple[str, Any, Any]]:
            if type(a)!=type(b): return [(path,a,b)]
            if isinstance(a,dict):
                return [item for key in a.keys()|b.keys() for item in differences(a.get(key),b.get(key),path+'.'+key)]
            if isinstance(a,list):
                return [(path+'.length',len(a),len(b))] if len(a)!=len(b) else [item for i,(x,y) in enumerate(zip(a,b)) for item in differences(x,y,path+f'.{i}')]
            return [] if a==b else [(path,a,b)]
        raise AssertionError(json.dumps(differences(expected,actual)[:8],indent=2,default=str))
    return expected

def presence(conn: sqlite3.Connection, state: str) -> None:
    identity=dict(protocol_version=PROTOCOL_VERSION,spool_format_version=SPOOL_FORMAT_VERSION,
        application_revision='synthetic',executor_incarnation='synthetic',connection_generation=0)
    if state=='incompatible':identity['protocol_version']=0
    lease=WALL if state!='expired' else WALL-int(MAC_AWAY_AFTER_SECONDS*1000)
    sleep=WALL if state=='asleep' else None
    conn.execute('INSERT INTO mac_executor_presence(singleton,executor_incarnation,identity_json,last_contact_at_ms,last_lease_at_ms,continuous_since_ms,going_to_sleep_at_ms) VALUES (1,?,?,?,?,?,?)',
        ('synthetic',json.dumps(identity),WALL,lease,WALL,sleep))
    if state=='refused':
        refusal=dict(reason='Mac needs update',peer_identity=identity,
            server_identity=dict(protocol_version=PROTOCOL_VERSION,spool_format_version=SPOOL_FORMAT_VERSION,application_revision='server',executor_incarnation='server'),
            refused_at_ms=WALL)
        conn.execute('UPDATE mac_executor_presence SET refusal_json=? WHERE singleton=1',(json.dumps(refusal),))

def check_readiness(conn: sqlite3.Connection, root: Path, outcome: str) -> dict[str, Any]:
    ticket=new_ticket(conn,'new_worker' if outcome=='user_opened' else 'coding')
    fieldless=None
    if outcome in ('present','expired','asleep','incompatible','absent','refused'):
        conn.execute("UPDATE tickets SET locale='macos' WHERE id=?",(ticket.id,))
        if outcome!='absent':presence(conn,outcome)
    elif outcome=='backlogged':conn.execute('UPDATE tickets SET is_backlogged=1 WHERE id=?',(ticket.id,))
    elif outcome=='terminal':conn.execute("UPDATE tickets SET stage='done' WHERE id=?",(ticket.id,))
    elif outcome=='out':tickets.claim_ticket_for_worker_step(conn,ticket.id,readiness_check=readiness.is_ready_for_worker_step,now=NOW)
    elif outcome=='user_opened':conn.execute('INSERT INTO ticket_paired_stage_openers VALUES (?,?,?)',(ticket.id,ticket.stage,NOW))
    elif outcome=='disabled':conn.execute('INSERT INTO backend_model_enablement VALUES (?,?,0)',(ticket.employee_backend,ticket.employee_launch_model))
    elif outcome=='fieldless':
        workflow=dataclasses.replace(ticket.workflow,stages=tuple(dataclasses.replace(stage,gating_field=None) if stage.id==ticket.stage else stage for stage in ticket.workflow.stages))
        fieldless=dataclasses.replace(ticket,workflow=workflow)
        payload=json.loads(conn.execute('SELECT workflow_json FROM tickets WHERE id=?',(ticket.id,)).fetchone()[0])
        for stage in payload['stages']:
            if stage['id']==ticket.stage:stage['gating_field']=None
        # Deliberately invalid synthetic row. Production commit projection rejects it.
        with sqlite3.connect(root/'python.sqlite') as raw:
            raw.execute('UPDATE tickets SET workflow_json=? WHERE id=?',(json.dumps(payload),ticket.id))
    def python() -> dict[str, Any]:
        with patch('planner.runtime.worker_step_readiness.now_ms',return_value=WALL):
            current=fieldless or tickets.read_ticket(conn,ticket.id)
            reason=readiness.worker_step_blocker(conn,current,worker_type_definition=current.workflow)
            error=None
            try:claimed=tickets.claim_ticket_for_worker_step(conn,ticket.id,readiness_check=readiness.is_ready_for_worker_step,now=NOW) is not None
            except PlannerError as caught:
                claimed=False
                error={'code':caught.code.value,'message':str(caught),'detail':caught.detail}
            return {'ready':reason is None,'reason':reason,'claimed':claimed,'error':error,'rows':rows(conn)}
    spec: dict[str, Any]={'mode':'readiness','ticket_id':ticket.id}
    if fieldless is not None:
        spec['fieldless_snapshot']=plain(fieldless)
        spec['fieldless_snapshot']['ticket_status']=fieldless.ticket_status.value
    result=compare(conn,root,spec,python)
    return {key:result[key] for key in ('ready','reason','claimed','error','rows')}

async def python_abort(conn: sqlite3.Connection, root: Path, spec: dict[str, Any], values: ConversationStartValues | None) -> dict[str, Any]:
    entered=asyncio.Event()
    killed=[]
    class Hung(InMemoryConversationSystem):
        async def send(self,*args: Any,**kwargs: Any) -> PromptDeliveryFate:
            entered.set()
            return await asyncio.Future[PromptDeliveryFate]()
        async def kill(self,conversation_id: str) -> None:
            killed.append(conversation_id)
            await super().kill(conversation_id)
    system=Hung()
    if spec['existing']:
        await system.start_conversation(ConversationStartRequest(conversation_id='parity_conversation',model='gpt-6.1-sol',workspace_folder=root))
    conversation_id='parity_conversation' if spec['existing'] else None
    if spec['kind']=='ticket':
        operation=start.send_to_ticket_conversation(system,conn,spec['ticket_id'],text_message_content('parity opener'),now=NOW,conversation_id=conversation_id,created_conversation_id='parity_conversation',sender_label='test')
    else:
        operation=start.send_to_agent_conversation(system,conn,spec['agent_key'],text_message_content('parity opener'),values,required_manager_id=spec.get('manager_id'),conversation_id=conversation_id,created_conversation_id='parity_conversation',sender_label='test')
    task=asyncio.create_task(operation)
    await entered.wait()
    task.cancel()
    aborted=False
    try:await task
    except asyncio.CancelledError:aborted=True
    return {'aborted':aborted,'killed':killed,'rows':rows(conn)}

def check_abort(conn: sqlite3.Connection,root: Path,kind: str,existing: bool) -> dict[str, Any]:
    spec: dict[str, Any]={'mode':'abort','kind':kind,'existing':existing,'ticket_id':''}
    values: ConversationStartValues | None=None
    if kind=='ticket':
        ticket=new_ticket(conn)
        spec['ticket_id']=ticket.id
        tickets.claim_ticket_for_worker_step(conn,ticket.id,readiness_check=readiness.is_ready_for_worker_step,now=NOW)
        if existing:
            system=InMemoryConversationSystem()
            asyncio.run(start.start_ticket_conversation(system,conn,ticket,start.worker_resolve(conn,ticket),conversation_id='parity_conversation',now=NOW))
    else:
        if kind=='manager':
            manager=managers.create_manager(conn,title='Parity Manager',project_id='project_other',clock=RuntimeTestClock(datetime.fromtimestamp(NOW,timezone.utc)))
            spec.update(agent_key='manager_'+manager.id,manager_id=manager.id)
            values=start.manager_resolve(conn,manager)
        else:
            spec['agent_key']='chief'
            values=start.agent_resolve(conn)
        if existing:
            asyncio.run(start.start_agent_conversation(InMemoryConversationSystem(),conn,spec['agent_key'],values,conversation_id='parity_conversation',required_manager_id=spec.get('manager_id')))
        spec['values']=plain(values)
    conn.commit()
    result=compare(conn,root,spec,lambda:asyncio.run(python_abort(conn,root,spec,values)))
    return {'aborted':result['aborted'],'killed':result['killed'],'rows':result['rows']}

def main() -> None:
    started=time.monotonic()
    results=[]
    cases=[('readiness',name,False) for name in ('ready','backlogged','absent','incompatible','refused','present','expired','asleep','terminal','out','user_opened','disabled','fieldless')]
    cases += [('abort',kind,existing) for kind in ('ticket','chief','manager') for existing in (False,True)]
    with tempfile.TemporaryDirectory(prefix='worker-python-ts-') as temporary:
        storage=Path(temporary)
        with synthetic_home_environment(create_synthetic_homes(storage)):
            for mode,name,existing in cases:
                root=storage/(mode+'-'+name+('-existing' if existing else ''))
                root.mkdir()
                conn=connect(str(root/'python.sqlite'))
                try:
                    create_schema(conn)
                    with register_workspace_root(str(root/'python.sqlite'),root):
                        result=check_readiness(conn,root,name) if mode=='readiness' else check_abort(conn,root,name,existing)
                    results.append({'mode':mode,'case':name,'existing':existing,'answer':result})
                finally:conn.close()
    print(json.dumps({'match':True,'seconds':round(time.monotonic()-started,3),'cases':results},indent=2,default=str))

def test_worker_runtime_parity() -> None:
    main()

if __name__=='__main__':main()
