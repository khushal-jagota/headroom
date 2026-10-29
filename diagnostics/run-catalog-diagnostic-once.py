"""One authorized attempt, only after independent application/readiness gates."""
import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from planner.conversation.model_resolution import diagnostic_contracts
from planner.executor import catalog_diagnostic_operator

BASE = 'https://panels-vps.tail687c57.ts.net:9443'
BODY = {'ticket_id': 't_mw0fvv1n'}
SSH = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o',
       'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
       'khushaljagota@100.118.172.84']
REMOTE = '''import json,os,subprocess,sys
from uuid import UUID
if any(k in os.environ for k in ('PLAN_EXECUTOR_CHILD_ID','PLAN_EXECUTOR_CAPABILITY')):
    sys.exit(71)
payload=json.load(sys.stdin)
env=dict(os.environ)
env['PLAN_EXECUTOR_ROOT']=(
    '/Users/khushaljagota/Library/Application Support/Panels/executor-dogfood-t_0471dgaa'
)
cmd=['/Users/khushaljagota/Deployments/PanelsExecutor/venv/bin/panels','executor']
if payload['operation']=='arm':
    cmd+=['catalog-diagnostic-arm']
    data=json.dumps(payload['arm'])
elif payload['operation']=='result':
    UUID(payload['request_id'])
    cmd+=['catalog-diagnostic-result','--request-id',payload['request_id']]
    data=''
else:
    sys.exit(72)
done=subprocess.run(cmd,input=data,text=True,capture_output=True,env=env)
sys.stdout.write(done.stdout)
sys.stderr.write(done.stderr)
sys.exit(done.returncode)
'''


def main():
    if sys.flags.isolated != 1:
        raise ValueError('Use the reviewed interpreter with -I')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', required=True, type=Path)
    parser.add_argument('--expected-prefix', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--private-output', required=True, type=Path)
    args = parser.parse_args()
    if Path(sys.prefix).resolve() != args.expected_prefix.resolve():
        raise ValueError('Use the reviewed installed package interpreter')
    binding = catalog_diagnostic_operator.read_binding(json.loads(args.binding.read_text()))
    sources = {
        'operator_script': Path(__file__).read_bytes(),
        'operator_module': Path(catalog_diagnostic_operator.__file__).read_bytes(),
        'receipt_contract': Path(diagnostic_contracts.__file__).read_bytes(),
    }
    catalog_diagnostic_operator.verify_sources(binding, sources)
    os.umask(0o077)
    # Exclusive directories prevent overwrites or continuation of an old attempt.
    args.output.mkdir(parents=True, mode=0o700)
    args.private_output.mkdir(parents=True, mode=0o700)
    receipt = {'source': binding.application_revision, 'retry': False, 'rearm': False,
               'started_at': datetime.datetime.now(datetime.UTC).isoformat()}

    def private_json(name, value):
        fd = os.open(args.private_output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')

    def http(path, body, headers):
        req = Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json', **headers},
                      method='GET' if body is None else 'POST')
        with urlopen(req, timeout=45) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('response exceeds bound')
            return response.status, json.loads(raw)

    def native_once(operation, payload):
        receipt[operation + '_attempted'] = True
        done = subprocess.run(SSH + ['/usr/bin/python3 -c ' + shlex.quote(REMOTE)],
                              input=json.dumps({'operation': operation, **payload}),
                              text=True, capture_output=True, timeout=20)
        private_json(operation + '-transport.json', {
            'returncode': done.returncode, 'stdout_bytes': len(done.stdout.encode()),
            'stderr_bytes': len(done.stderr.encode()),
        })
        if done.returncode:
            raise RuntimeError('native operator command failed')
        if len(done.stdout.encode()) > 4096:
            raise ValueError('native output exceeds bound')
        return json.loads(done.stdout)

    def preserve_ordinary(ordinary):
        private_json('ordinary-result.json', ordinary)
        validation = ordinary['validation']
        selected = validation['selection']
        if (selected['submitted_token'] != 'opus'
                or selected['selection_kind'] != 'legacy_unclassified'
                or selected['reasoning_effort'] is not None):
            raise ValueError('Ordinary selection differs from reviewed attempt')
        receipt['ordinary_state'] = validation['state']
        receipt['ordinary_reason'] = validation['reason']

    def preserve_finite(value):
        with (args.output / 'diagnostic-result.json').open('x') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')

    try:
        catalog_diagnostic_operator.run_once(
            binding=binding, sources=sources, http=http, native_once=native_once, body=BODY,
            expected_workspace={
                'workspace_folder': '/Users/khushaljagota/panels-dogfood-t_0471dgaa',
                'workspace_path_policy': 'native_absolute', 'workspace_home_relative': None,
            },
            preserve_ordinary=preserve_ordinary, preserve_finite=preserve_finite,
        )
        receipt.update(outcome='finite_record_retrieved_once', native_receipt_consumed=True)
    except Exception as error:
        receipt.update(outcome='inconclusive_stop', failure_type=type(error).__name__,
                       consumption='consumed_or_uncertain_do_not_retry')
        raise
    finally:
        receipt['completed_at'] = datetime.datetime.now(datetime.UTC).isoformat()
        receipt['discovery_stopped'] = True
        with (args.output / 'diagnostic-operation-receipt.json').open('x') as stream:
            json.dump(receipt, stream, indent=2)
            stream.write('\n')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Operation stopped. Read the bounded receipt; do not retry.')
        raise SystemExit(1) from None
    print('One reserved preview and one validated finite receipt completed. No retry.')
