# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Create a real normally-hooked focused commit, then run an unchanged tox lane."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

cwd = Path.cwd().resolve()
control = cwd.parent / 'runner-control'
spec = json.loads((control / '.ci/adk-annotated-spec.json').read_text())
ev = cwd.parent / 'validation-evidence'
ev.mkdir(exist_ok=True)
phase = sys.argv[1]
assert phase in ('prepare','tests','after')
record = {'phase': phase, 'started_at': datetime.now(timezone.utc).isoformat(), 'steps': [],
          'full_tox_command_completed': False, 'base': spec['base'], 'scope_id': spec['scope_id']}
env = dict(os.environ)
env.pop('SKIP', None)
env.pop('PYTHONPATH', None)
env.pop('UV_PROJECT_ENVIRONMENT', None)
assert env['UV_EXCLUDE_NEWER'] == spec['resolution_cutoff']
uv = shutil.which('uv')
assert uv and subprocess.check_output([uv,'--version']).decode().split()[:2] == ['uv','0.12.23']
env['TOX_UV_PATH'] = uv
record['TOX_UV_PATH'] = uv


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git(*args):
    return subprocess.check_output(['git',*args], cwd=cwd)


def proof(committed=False):
    for name, digest in spec['configuration_sha256'].items():
        assert (cwd / name).read_bytes() == git('show',spec['base']+':'+name)
        assert sha((cwd/name).read_bytes()) == digest
    for name, digest in spec['source_sha256'].items():
        assert sha((cwd/name).read_bytes()) == digest
        assert git('hash-object',name).decode().strip() == spec['source_git_blobs'][name]
    head = git('rev-parse','HEAD').decode().strip()
    if committed:
        assert git('rev-parse','HEAD^').decode().strip() == spec['base']
        patch = git('diff','--full-index','--binary',spec['base'],'HEAD')
        assert not git('status','--porcelain').strip()
    else:
        assert head == spec['base']
        patch = git('diff','--cached','--full-index','--binary')
        assert not git('diff','--name-only').strip()
    assert sha(patch) == spec['patch_sha256']
    return {'head':head,'patch_sha256':sha(patch),'source_sha256':spec['source_sha256'],
            'configuration_sha256':spec['configuration_sha256'],'source_configuration_unchanged':True}


def run(name,args,expected=0):
    started = time.monotonic()
    path = ev / (name+'.log')
    lines = []
    with path.open('w',encoding='utf-8') as log:
        p = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,text=True)
        for line in p.stdout:
            print(line,end='',flush=True)
            log.write(line)
            if re.search(r'^=+ .*\b(?:passed|failed)\b.*\bin [\d.]+s',line):
                lines.append(line.strip())
        code = p.wait()
    record['steps'].append({'name':name,'args':args,'exit_code':code,'elapsed_seconds':round(time.monotonic()-started,2),
                           'log':path.name,'pytest_summaries':lines})
    if code != expected:
        raise subprocess.CalledProcessError(code,args)
    return path.read_text()


def origins(python,digests,label):
    mapping = [(n, str(cwd/n), d) for n,d in digests.items() if n.startswith('src/')]
    code = (
        'import importlib,hashlib,json,sys\nfrom pathlib import Path\n'
        'items=json.loads(sys.argv[1]);results=[]\n'
        'for name,path,digest in items:\n'
        ' m=importlib.import_module(name.removeprefix("src/").removesuffix(".py").replace("/","."))\n'
        ' actual=Path(m.__file__).resolve();expected=Path(path).resolve()\n'
        ' value=hashlib.sha256(actual.read_bytes()).hexdigest()\n'
        ' results.append({"module":name,"origin":str(actual),"expected":str(expected),"sha256":value})\n'
        ' assert actual==expected and value==digest\n'
        'print(json.dumps({"python":sys.version,"imports":results}))\n'
        'assert sys.version_info[:2]==tuple(map(int,sys.argv[2].split(".")))\n'
    )
    run(label,[python,'-c',code,json.dumps(mapping),env['PYTHON_VERSION']])


def native_export():
    head = git('rev-parse','HEAD').decode().strip()
    raw = git('cat-file','commit',head)
    assert hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == head
    patch = git('diff','--full-index','--binary',spec['base'],'HEAD')
    data = {'head':head,'tree':git('rev-parse','HEAD^{tree}').decode().strip(),'base':spec['base'],
            'commit_raw_base64':base64.b64encode(raw).decode(),'patch_sha256':sha(patch),
            'source_sha256':{n:sha((cwd/n).read_bytes()) for n in spec['files']},
            'git_blobs':{n:git('rev-parse','HEAD:'+n).decode().strip() for n in spec['files']},
            'normal_commit_hook_proven':record['normal_commit_hook_proven'],'author':spec['author'],'committer':spec['committer']}
    (ev/'native-export.json').write_text(json.dumps(data,indent=2))
    print('NATIVE_OCT15_EXPORT '+json.dumps(data),flush=True)


try:
    if phase == 'prepare':
        assert git('rev-parse','HEAD').decode().strip() == spec['base']
        assert not git('status','--porcelain').strip()
        patch = control / '.ci/adk-annotated.patch'
        assert sha(patch.read_bytes()) == spec['patch_sha256']
        run('apply-reviewed-patch',['git','apply','--index','--whitespace=error',str(patch)])
        record['source_before'] = proof()
        run('declared-venv',[uv,'venv','--python','python'+env['PYTHON_VERSION'],'.venv'])
        run('declared-all-extras',[uv,'sync','--all-extras'])
        lock = (cwd/'uv.lock').read_bytes()
        (ev/'generated-uv.lock').write_bytes(lock)
        record['generated_lock_sha256'] = sha(lock)
        python = str(cwd/'.venv/bin/python')
        production = {n:(cwd/n).read_bytes() for n in spec['files'] if n.startswith('src/')}
        try:
            baseline = {}
            for name in production:
                value = git('show',spec['base']+':'+name)
                (cwd/name).write_bytes(value)
                baseline[name] = sha(value)
            origins(python,baseline,'baseline-import-origins')
            text = run('same-regression-baseline',[python,'-m','pytest',*spec['regression_args']],expected=1)
            assert re.search(r'\b'+str(spec['expected_regression_failures'])+r' failed\b',re.sub(r'\x1b\[[0-9;]*m','',text)), 'Expected genuine consuming-path failures.'
            record['same_regression_baseline_failed_count'] = spec['expected_regression_failures']
        finally:
            for name,value in production.items():
                (cwd/name).write_bytes(value)
        record['restored_source'] = proof()
        origins(python,spec['source_sha256'],'patched-import-origins')
        run('focused-owning-tests',[python,'-m','pytest',*spec['focused_tests']])
        run('precommit-tool-install',[uv,'tool','install','pre-commit'])
        run('tox-tool-install',[uv,'tool','install','tox==4.65.3','--with','tox-uv==1.37.0'])
        bindir = Path(subprocess.check_output([uv,'tool','dir','--bin']).decode().strip())
        precommit = str(bindir/'pre-commit')
        run('normal-hook-install',[precommit,'install'])
        try:
            run('normal-scoped-hooks',[precommit,'run','--files',*spec['files']])
        finally:
            current = git('diff','--cached','--full-index','--binary')
            unstaged = git('diff','--full-index','--binary')
            record['hook_source_patch_sha256'] = sha(current)
            record['hook_unstaged_delta'] = unstaged.decode()
            if unstaged:
                print('REJECTED_FORMATTER_DELTA '+base64.b64encode(unstaged).decode(),flush=True)
        proof()
        message = control / '.ci/adk-annotated-message.txt'
        assert sha(message.read_bytes()) == spec['message_sha256']
        run('local-author-name',['git','config','user.name',spec['committer']['name']])
        run('local-author-email',['git','config','user.email',spec['committer']['email']])
        env.update(GIT_AUTHOR_NAME=spec['author']['name'],GIT_AUTHOR_EMAIL=spec['author']['email'],
            GIT_AUTHOR_DATE=spec['author']['date'],GIT_COMMITTER_NAME=spec['committer']['name'],
            GIT_COMMITTER_EMAIL=spec['committer']['email'],GIT_COMMITTER_DATE=spec['committer']['date'],TZ='UTC',
            GIT_TRACE2_EVENT=str(ev/'normal-commit-trace.jsonl'))
        run('normal-source-commit',['git','-c','commit.gpgsign=false','commit','-F',str(message)])
        events,malformed = [],[]
        for number,line in enumerate((ev/'normal-commit-trace.jsonl').read_text().splitlines(),1):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                malformed.append({'line':number,'text':line})
        starts = [e for e in events if e.get('event')=='child_start' and
                  (e.get('hook_name')=='pre-commit' or any('hooks/pre-commit' in str(x) for x in e.get('argv',[])))]
        assert starts and all(any(e.get('event')=='child_exit' and e.get('sid')==s.get('sid') and
             e.get('child_id')==s.get('child_id') and e.get('code')==0 for e in events) for s in starts)
        record.update(normal_commit_hook_proven=True,actual_hook_starts=starts,trace_malformed_lines=malformed)
        record['source_after'] = proof(committed=True)
        native_export()
    elif phase == 'tests':
        native = json.loads((ev/'native-export.json').read_text())
        assert git('rev-parse','HEAD').decode().strip() == native['head']
        record['source_before'] = proof(committed=True)
        bindir = Path(subprocess.check_output([uv,'tool','dir','--bin']).decode().strip())
        tox = str(bindir/'tox')
        run('pristine-tox-lane',[tox,'--notest','-e',env['TOX_LANE']])
        origins(str(cwd/'.tox'/env['TOX_LANE']/'bin/python'),spec['source_sha256'],'pristine-tox-import-origins')
        try:
            run('full-unchanged-tox-lane',[tox,'-e',env['TOX_LANE']])
        finally:
            record['full_tox_command_completed'] = any(x['name']=='full-unchanged-tox-lane' for x in record['steps'])
        record['source_after'] = proof(committed=True)
    else:
        if (ev/'native-export.json').exists():
            record['source_after'] = proof(committed=True)
        if (ev/'generated-uv.lock').exists():
            lock = (cwd/'uv.lock').read_bytes()
            assert lock == (ev/'generated-uv.lock').read_bytes()
            record.update(generated_lock_sha256=sha(lock),generated_lock_unchanged=True)
    record['state'] = 'passed'
except BaseException as exc:
    record.update(state='failed',error=repr(exc))
    raise
finally:
    record['completed_at'] = datetime.now(timezone.utc).isoformat()
    (ev/(phase+'.json')).write_text(json.dumps(record,indent=2))
    print('ADK_OCT15_PHASE '+json.dumps(record),flush=True)
