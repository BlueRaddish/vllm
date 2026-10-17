"""Verify an existing GitHub integration preview; no commits, refs, or API writes."""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

CONTROL=Path(__file__).resolve().parent
PROOFS=Path('/tmp/oct17-vc-cmux-preview-proofs')
PROOFS.mkdir(exist_ok=True)
SPEC=json.loads((CONTROL/'manifest.json').read_text())
record={'started_at':datetime.now(timezone.utc).isoformat(),'commands':[], 'public_mutations':[],
        'new_source_commits':[], 'preview_authored_by_us':False}

def require(condition,message):
    if not condition: raise RuntimeError(message)

def sha(raw): return hashlib.sha256(raw).hexdigest()

def save(): (PROOFS/'receipt.json').write_text(json.dumps(record,indent=2))

def run(args,cwd,*,expected=0,timeout=600,env=None):
    result=subprocess.run(args,cwd=cwd,env=env or os.environ.copy(),stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,timeout=timeout)
    require(len(result.stdout)<=2*1024**2,'Command log exceeds bound.')
    entry={'command':args,'cwd':str(cwd),'exit_code':result.returncode,
           'stdout':result.stdout.decode('utf-8','replace')}
    record['commands'].append(entry);save();print('OCT17_VC_COMMAND='+json.dumps(entry),flush=True)
    require(result.returncode==expected,'Unexpected exit code: '+json.dumps(args))
    return result.stdout

def fetch(url,limit):
    with urllib.request.urlopen(url,timeout=60) as response: raw=response.read(limit+1)
    require(len(raw)<=limit,'Download exceeds bound.');return raw

def setup(cwd):
    tools=Path('/tmp/oct17-vc-tools');tools.mkdir(exist_ok=True)
    url='https://github.com/astral-sh/uv/releases/download/0.12.23/uv-x86_64-unknown-linux-gnu.tar.gz'
    archive=fetch(url,40*1024**2);checksum=fetch(url+'.sha256',4096).decode().split()[0]
    require(sha(archive)==checksum,'Official pinned uv checksum differs.')
    archive_path=tools/'uv.tar.gz';archive_path.write_bytes(archive)
    with tarfile.open(archive_path) as tar:
        rows=[m for m in tar.getmembers() if m.isfile() and m.name.endswith('/uv')]
        require(len(rows)==1 and rows[0].size<100*1024**2,'Unexpected uv archive.')
        binary=tools/'uv';binary.write_bytes(tar.extractfile(rows[0]).read());binary.chmod(0o755)
    os.environ['PATH']=str(tools)+':'+os.environ['PATH']
    run(['uv','--version'],cwd)
    env_path=Path('/tmp/oct17-vc-python')
    run(['uv','venv','--python','3.12',str(env_path)],cwd)
    run(['uv','pip','install','--python',str(env_path/'bin/python'),'PyYAML'],cwd)
    os.environ['PATH']=str(env_path/'bin')+':'+os.environ['PATH']
    record['uv_archive_sha256']=checksum;save()

def guard(cwd):
    require(run(['git','rev-parse','HEAD'],cwd).decode().strip()==SPEC['preview'],'Preview differs.')
    require(run(['git','show','-s','--format=%P','HEAD'],cwd).decode().split()==[SPEC['preview_base'],SPEC['pr_head']],
            'Preview parents differ.')
    require(run(['git','rev-parse','HEAD^{tree}'],cwd).decode().strip()==SPEC['preview_tree'],'Preview tree differs.')
    for file in SPEC['files']:
        raw=(cwd/file['path']).read_bytes()
        require(sha(raw)==file['sha256'],'Frozen preview source differs: '+file['path'])
        require(run(['git','rev-parse','HEAD:'+file['path']],cwd).decode().strip()==file['blob'],
                'Frozen source/blob association differs.')
    require(not run(['git','status','--porcelain'],cwd).strip(),'Preview tree is dirty.')

def main():
    require(SPEC['repo']=='manaflow-ai/cmux' and SPEC['pull_number']==21130,'Unexpected target.')
    require(SPEC['pr_head']=='eaea2720b61c3a7d283cb2cc27e378daa1412394','PR head changed.')
    require(not any(os.environ.get(k) for k in ('GH_TOKEN','GITHUB_TOKEN','SKIP','PRE_COMMIT_ALLOW_NO_CONFIG')),
            'Unexpected secret or bypass environment.')
    # A crashing child must be retained as failure, not silently read as success.
    try: run([sys.executable,'-c','import sys;sys.exit(23)'],CONTROL,timeout=30)
    except RuntimeError: require(record['commands'][-1]['exit_code']==23,'Crash control missing.')
    else: raise RuntimeError('Failed child was accepted.')
    require(SPEC['preview']!=SPEC['pr_head'],'Preview is not an integration commit.')
    cwd=Path('/tmp/oct17-vc-cmux-preview')
    require(not cwd.exists(),'Isolated checkout already exists.')
    cwd.mkdir()
    run(['git','init','--initial-branch=oct17-vc-preview'],cwd)
    run(['git','remote','add','origin','https://github.com/manaflow-ai/cmux.git'],cwd)
    run(['git','fetch','--depth=2','origin',SPEC['preview']],cwd,timeout=300)
    require(run(['git','rev-parse','FETCH_HEAD'],cwd).decode().strip()==SPEC['preview'],
            'Fetched immutable integration preview differs.')
    run(['git','checkout','--detach','FETCH_HEAD'],cwd)
    guard(cwd);setup(cwd)
    run(['bash','scripts/install-git-hooks.sh'],cwd)
    hook_path=Path(run(['git','config','--get','core.hooksPath'],cwd).decode().strip())
    effective_hooks=(hook_path if hook_path.is_absolute() else cwd/hook_path).resolve()
    require(effective_hooks==(cwd/'.git/cmux-git-hooks').resolve(),'Normal trusted hooks not installed.')
    require((effective_hooks/'pre-commit').is_file() and os.access(effective_hooks/'pre-commit',os.X_OK),
            'Effective normal pre-commit hook is not executable.')
    record['effective_pre_commit']={'path':str(effective_hooks/'pre-commit'),
                                    'sha256':sha((effective_hooks/'pre-commit').read_bytes())};save()
    hook_cwd=Path('/tmp/oct17-vc-cmux-hook-scope')
    run(['git','worktree','add','--detach',str(hook_cwd),SPEC['preview_base']],cwd)
    try:
        for file in SPEC['patch_files']:
            shutil.copyfile(cwd/file,hook_cwd/file)
        run(['git','add','--',*SPEC['patch_files']],hook_cwd)
        require(run(['git','diff','--cached','--name-only'],hook_cwd).decode().splitlines()==sorted(SPEC['patch_files']),
                'Scoped hook index differs.')
        run(['git','diff','--cached','--check'],hook_cwd)
        trace=PROOFS/'pre-commit.trace.jsonl'
        run(['git','hook','run','pre-commit'],hook_cwd,env={**os.environ,'GIT_TRACE2_EVENT':str(trace)})
        events=[json.loads(line) for line in trace.read_text().splitlines()]
        starts=[e for e in events if e.get('event')=='child_start' and e.get('child_class')=='hook'
                and e.get('hook_name')=='pre-commit']
        require(len(starts)==1,'Exactly one real normal pre-commit invocation was not traced.')
        exits=[e for e in events if e.get('event')=='child_exit' and e.get('sid')==starts[0]['sid']
               and e.get('child_id')==starts[0]['child_id']]
        require(len(exits)==1 and exits[0]['code']==0,'Real normal pre-commit did not pass.')
        record['normal_hook_trace']={'start':starts[0],'exit':exits[0],'trace_sha256':sha(trace.read_bytes())};save()
        require(run(['git','diff','--cached','--numstat'],hook_cwd).decode()==SPEC['patch_numstat'],
                'Normal hook changed the approved source scope.')
        for file in SPEC['files'][:2]:
            require(run(['git','rev-parse',':'+file['path']],hook_cwd).decode().strip()==file['blob']
                    and sha((hook_cwd/file['path']).read_bytes())==file['sha256'],
                    'Normal hook changed a frozen patched blob.')
    finally:
        run(['git','worktree','remove','--force',str(hook_cwd)],cwd)
    for args in SPEC['test_commands']: run(args,cwd,timeout=900)
    guard(cwd)
    record.update(state='passed exact existing integration preview',preview=SPEC['preview'],
                  preview_base=SPEC['preview_base'],pr_head=SPEC['pr_head'],
                  source_patch_sha256=SPEC['source_patch_sha256'],
                  completed_at=datetime.now(timezone.utc).isoformat(),
                  limits=SPEC['limits'])
    save();print('OCT17_VC_RESULT='+json.dumps(record),flush=True)

if __name__=='__main__':
    try: main()
    except BaseException as error:
        record.update(state='failed or unreachable; inspect actual commands',error=repr(error));save();raise
