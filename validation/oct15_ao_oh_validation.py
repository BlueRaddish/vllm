"""Validate frozen SDK behavior and its paired hook-guide documentation."""
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
        'scope':'sdk agent-hook ambient-plugin isolation','real_model_calls':False}

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
    print('OH_OCT15_NATIVE '+json.dumps({'scope':label,**value}),flush=True)
    return value

def commit(scope,label,hooks_required):
    person=spec['person']
    run('configure name',['git','config','user.name',person['name']])
    run('configure email',['git','config','user.email',person['email']])
    env.update(GIT_AUTHOR_NAME=person['name'],GIT_AUTHOR_EMAIL=person['email'],GIT_AUTHOR_DATE=person['date'],
       GIT_COMMITTER_NAME=person['name'],GIT_COMMITTER_EMAIL=person['email'],GIT_COMMITTER_DATE=person['date'])
    trace=cwd.parent/(label+'-commit-trace.jsonl')
    env['GIT_TRACE2_EVENT']=str(trace)
    run('normal '+label+' commit',['git','-c','commit.gpgsign=false','commit','-m',scope['message']])
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
    run('apply frozen SDK patch',['git','apply','--index','--whitespace=error',str(patch)])
    proof(sdk)
    run('declared frozen SDK development install',['uv','sync','--frozen','--group','dev','--python','3.13'])
    run('fixed consuming regression control',['uv','run','--no-sync','pytest','tests/sdk/hooks/test_agent_hook_plugins.py'])
    production={n:(cwd/n).read_bytes() for n in sdk['files'] if n.startswith('openhands-sdk/')}
    try:
        for name in production:(cwd/name).write_bytes(git('show',sdk['base']+':'+name))
        import_proof(sdk,{n:sha((cwd/n).read_bytes()) for n in production},'actual baseline source imports')
        red=run('actual baseline hook regressions',['uv','run','--no-sync','pytest','tests/sdk/hooks/test_agent_hook_plugins.py'],expected=1)
        clean=re.sub(r'\x1b\[[0-9;]*m','',red.stdout+red.stderr)
        assert re.search(r'\b11 failed\b',clean), 'All eleven behavioral cases must genuinely reproduce'
        assert not re.search(r'TypeError:|ERROR collecting|ERROR at setup|ImportError:|ModuleNotFoundError:',clean), 'Regression failed before its hook behavior'
    finally:
        for name,value in production.items():(cwd/name).write_bytes(value)
    record['restored_source']=proof(sdk)
    import_proof(sdk)
    run('owning hook and conversation plugin tests',['uv','run','--no-sync','pytest','tests/sdk/hooks/','tests/sdk/conversation/test_local_conversation_plugins.py'])
    run('install ordinary repository hooks',['uv','run','--no-sync','pre-commit','install'])
    try:
        run('normal scoped repository hooks',['uv','run','--no-sync','pre-commit','run','--files',*sdk['files']])
    finally:
        delta=git('diff','--binary','--full-index')
        if delta:print('OH_OCT15_REJECTED_FORMATTER_DELTA '+base64.b64encode(delta).decode(),flush=True)
    proof(sdk)
    record['sdk_native']=commit(sdk,'sdk',True)

    cwd=cwd.parent/'docs'
    docs=spec['docs']
    assert git('rev-parse','HEAD').decode().strip()==docs['base'] and not git('status','--porcelain').strip()
    patch=control/'oh-docs.patch';assert sha(patch.read_bytes())==docs['patch_sha256']
    run('apply paired guide patch',['git','apply','--index','--whitespace=error',str(patch)],directory=cwd)
    proof(docs)
    run('install documented Mint CLI',['npm','install','-g','mint@latest'],directory=cwd)
    run('record actual Mint CLI version',['mint','--version'],directory=cwd)
    links=run('documented real Mintlify internal link check',['mint','broken-links'],directory=cwd,expected=None)
    if links.returncode:
        fixed=(cwd/docs['files'][0]).read_bytes()
        try:
            (cwd/docs['files'][0]).write_bytes(git('show',docs['base']+':'+docs['files'][0]))
            baseline=run('same docs baseline link check',['mint','broken-links'],directory=cwd,expected=None)
            record['docs_baseline_link_exit_code']=baseline.returncode
        finally:(cwd/docs['files'][0]).write_bytes(fixed)
        raise AssertionError('Mintlify link validation failed; baseline result retained separately')
    proof(docs)
    record['docs_native']=commit(docs,'docs',False)
    record['state']='passed SDK behavior, normal hooks, paired documentation link check'
except BaseException as error:
    record.update(state='failed_or_partial',error=repr(error));raise
finally:
    record['finished_at']=datetime.now(timezone.utc).isoformat()
    print('OH_OCT15_RESULT '+json.dumps(record),flush=True)
