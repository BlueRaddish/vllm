"""Validate actual loopback response-stream errors and normally hooked source."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

control=Path(__file__).resolve().parent
spec=json.loads((control/'oh-spec.json').read_text())
cwd=Path.cwd().resolve()
env=dict(os.environ)
for key in ('SKIP','PYTHONPATH','UV_EXCLUDE_NEWER','UV_PROJECT_ENVIRONMENT'):
    env.pop(key,None)
env.update(TZ='UTC',GIT_LFS_SKIP_SMUDGE='1',LITELLM_LOCAL_MODEL_COST_MAP='True')
record={'started_at':datetime.now(timezone.utc).isoformat(),'steps':[],
        'scope':'typescript GET response-stream error handling','real_model_calls':False}

def sha(value):return hashlib.sha256(value).hexdigest()

def run(label,args,directory=None,expected=0):
    directory=cwd if directory is None else directory
    value=subprocess.run(args,cwd=directory,env=env,capture_output=True,text=True)
    print(value.stdout,end='',flush=True)
    print(value.stderr,end='',file=sys.stderr,flush=True)
    record['steps'].append({'label':label,'args':args,'cwd':str(directory),
        'exit_code':value.returncode,'stdout_sha256':sha(value.stdout.encode()),
        'stderr_sha256':sha(value.stderr.encode())})
    if expected is not None:
        assert value.returncode==expected,(label,value.returncode)
    return value

def git(*args):return subprocess.check_output(['git',*args],cwd=cwd)

def proof(scope,committed=False):
    for name,digest in scope['configuration_sha256'].items():
        assert sha((cwd/name).read_bytes())==digest
        assert (cwd/name).read_bytes()==git('show',scope['base']+':'+name)
    for name,digest in scope['source_sha256'].items():
        assert sha((cwd/name).read_bytes())==digest
    assert not git('diff','--name-only').strip()
    if committed:
        assert git('rev-parse','HEAD^').decode().strip()==scope['base']
        patch=git('diff','--full-index','--binary',scope['base'],'HEAD')
        assert not git('status','--porcelain').strip()
    else:
        assert git('rev-parse','HEAD').decode().strip()==scope['base']
        patch=git('diff','--cached','--full-index','--binary')
    assert sha(patch)==scope['patch_sha256']
    return {'base':scope['base'],'head':git('rev-parse','HEAD').decode().strip(),
            'source_sha256':scope['source_sha256'],'configuration_sha256':scope['configuration_sha256'],
            'patch_sha256':sha(patch)}

def import_proof(scope,digests=None,label='actual source imports'):
    code='''import hashlib,json,sys
from pathlib import Path
import openhands.sdk.hooks.executor as executor
import openhands.sdk.conversation.impl.local_conversation as local
expected=json.loads(sys.argv[1]);root=Path.cwd().resolve()
for module in (executor,local):
 path=Path(module.__file__).resolve();name=str(path.relative_to(root))
 assert hashlib.sha256(path.read_bytes()).hexdigest()==expected[name]
 print(json.dumps({'module':module.__name__,'origin':str(path),'sha256':expected[name]}))
'''
    run(label,['uv','run','--no-sync','python','-c',code,json.dumps(scope['source_sha256'] if digests is None else digests)])

def native(scope,label):
    raw=git('cat-file','commit','HEAD')
    value=proof(scope,committed=True)
    value.update(commit_raw_base64=base64.b64encode(raw).decode(),
      tree=git('rev-parse','HEAD^{tree}').decode().strip(),
      git_blobs={n:git('rev-parse','HEAD:'+n).decode().strip() for n in scope['files']})
    assert hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==value['head']
    print('OH_OCT17_RESPONSE_NATIVE '+json.dumps({'scope':label,**value}),flush=True)
    return value

def commit(scope,label,hooks_required):
    person=spec['person']
    run('configure name',['git','config','user.name',person['name']])
    run('configure email',['git','config','user.email',person['email']])
    env.update(GIT_AUTHOR_NAME=person['name'],GIT_AUTHOR_EMAIL=person['email'],GIT_AUTHOR_DATE=person['date'],
       GIT_COMMITTER_NAME=person['name'],GIT_COMMITTER_EMAIL=person['email'],GIT_COMMITTER_DATE=person['date'])
    trace=cwd.parent/(label+'-commit-trace.jsonl')
    env['GIT_TRACE2_EVENT']=str(trace)
    run('normal '+label+' commit',['git','commit','-m',scope['message']])
    events=[json.loads(line) for line in trace.read_text().splitlines()]
    starts=[e for e in events if e.get('event')=='child_start' and (e.get('hook_name')=='pre-commit' or any('hooks/pre-commit' in str(a) for a in e.get('argv',[])))]
    if hooks_required:
        assert starts
        for s in starts:
            assert any(e.get('event')=='child_exit' and e.get('sid')==s.get('sid') and e.get('child_id')==s.get('child_id') and e.get('code')==0 for e in events)
    record[label+'_normal_commit_hook_starts']=starts
    return native(scope,label)

try:
    sdk=spec['sdk']
    assert git('rev-parse','HEAD').decode().strip()==sdk['base'] and not git('status','--porcelain').strip()
    patch=control/'oh-sdk.patch';assert sha(patch.read_bytes())==sdk['patch_sha256']
    run('apply exact reviewed response patch',['git','apply','--index','--whitespace=error',str(patch)])
    proof(sdk)
    run('declared frozen SDK development install',['uv','sync','--frozen','--group','dev','--python','3.13'])
    client=cwd/'clients/typescript'
    run('locked TypeScript install',['npm','ci'],directory=client)
    command=['npx','vitest','run','--config','vitest.config.ts','src/__tests__/http-client-response.test.ts']
    run('fixed actual loopback regressions and controls',command,directory=client)
    production='clients/typescript/src/client/http-client.ts'
    fixed=(cwd/production).read_bytes()
    try:
        (cwd/production).write_bytes(git('show',sdk['base']+':'+production))
        red=run('same actual loopback regressions on baseline',command,directory=client,expected=1)
        text=re.sub(r'\x1b\[[0-9;]*m','',red.stdout+red.stderr)
        assert re.search(r'2 failed',text) and re.search(r'2 passed',text)
        assert 'still pending' in text and 'rejected' in text
        assert not re.search(r'Cannot find|Failed to load|SyntaxError|TypeError|ReferenceError',text)
    finally:
        (cwd/production).write_bytes(fixed)
    proof(sdk)
    run('all required TypeScript owning units with coverage',['npm','run','test:coverage'],directory=client)
    run('actual TypeScript build',['npm','run','build'],directory=client)
    paths=[str(Path(n).relative_to('clients/typescript')) for n in sdk['files']]
    run('changed TypeScript format',['npx','prettier','--check',*paths],directory=client)
    run('changed TypeScript lint',['npx','eslint',*paths],directory=client)
    record['full_static_checks']=[]
    for label,command in [('required full TypeScript lint',['npm','run','lint']),('required full TypeScript format',['npm','run','format:check'])]:
        result=run(label,command,directory=client,expected=None)
        comparison={'name':label,'patched_exit_code':result.returncode}
        if result.returncode:
            new_test=sdk['files'][1]
            test_bytes=(cwd/new_test).read_bytes()
            try:
                (cwd/production).write_bytes(git('show',sdk['base']+':'+production))
                (cwd/new_test).unlink()
                baseline=run(label+' on pristine source',command,directory=client,expected=None)
                comparison['baseline_exit_code']=baseline.returncode
                assert baseline.returncode!=0,'Patched static failure is absent on the baseline'
            finally:
                (cwd/production).write_bytes(fixed)
                (cwd/new_test).write_bytes(test_bytes)
        record['full_static_checks'].append(comparison)
        proof(sdk)
    run('install ordinary hooks',['uv','run','--no-sync','pre-commit','install'])
    run('normal scoped repository hooks',['uv','run','--no-sync','pre-commit','run','--files',*sdk['files']])
    proof(sdk)
    record['sdk_native']=commit(sdk,'sdk',True)
    if any(check['patched_exit_code'] for check in record['full_static_checks']):
        record['state']='incomplete: required full static checks failed; actual baseline failures and native source proof retained'
        raise SystemExit(1)
    record['state']='passed actual loopback red/green controls, all units, build, format, lint and normal hooks'
except BaseException as error:
    if not record.get('state','').startswith('incomplete:'):
        record['state']='failed_or_partial'
    record['error']=repr(error)
    raise
finally:
    record['finished_at']=datetime.now(timezone.utc).isoformat()
    print('OH_OCT17_RESPONSE_RESULT '+json.dumps(record),flush=True)
