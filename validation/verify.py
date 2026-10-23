"""Compose the real merge, reproduce both pre-resolution failures and run ordinary tox/hooks."""
import base64,json,os,subprocess,sys
from pathlib import Path
from common import run,junit,sha
c=Path.cwd().resolve();v=Path(__file__).resolve().parent;s=json.loads((v/'spec.json').read_text());proof=c.parent/'proof';proof.mkdir()
record={'state':'pending','python':sys.version,'source_PR_pushed':False,'full_tox_passed':False}
def git(*args):return run(['git',*args],c)
def exact(committed=False):
 assert git('write-tree').decode().strip()==s['tree']
 assert not git('diff','--name-only').strip()
 for p,h in (s['source_sha256']|s['config_sha256']).items():assert sha((c/p).read_bytes())==h
 assert sha(git('diff','--cached','--full-index','--binary',s['main']))==s['patch_sha256']
 if committed:assert not git('status','--porcelain','--untracked-files=no').strip()
def tests(label,args,failures=0,expected_tests=None):
 output=run([str(c/'.venv/bin/python'),'-m','pytest',*args,'-q','--junitxml='+str(proof/(label+'.xml'))],c,expect=1 if failures else 0)
 result=junit(proof/(label+'.xml'),failures=failures,tests=expected_tests)
 if failures:assert b'ERROR collecting' not in output and b'ImportError:' not in output
 return result
try:
 assert git('rev-parse','HEAD').decode().strip()==s['head']
 assert not git('status','--porcelain').strip()
 run(['git','fetch','--no-tags','https://github.com/google/adk-python.git',s['main']],c)
 git('config','user.name',s['person']['name']);git('config','user.email',s['person']['email'])
 merge=run(['git','merge','--no-commit','--no-ff',s['main']],c,expect=1)
 assert b'CONFLICT (content)' in merge
 assert git('diff','--name-only','--diff-filter=U').decode().splitlines()==[s['resolution'][0]]
 for n,p in enumerate(s['resolution']):(c/p).write_bytes((v/('resolution-'+str(n)+'.py')).read_bytes())
 git('add','--',*s['resolution']);exact()
 record['caller_audit']=git('grep','-n','validate_schema(','--','src/google/adk').decode()
 run(['uv','venv','--python',os.environ['PYTHON_VERSION'],'.venv'],c)
 run(['uv','sync','--all-extras'],c)
 lock=sha((c/'uv.lock').read_bytes());record['generated_lock_sha256']=lock
 record['fixed']=tests('fixed',s['regression_args'],expected_tests=7)
 target=c/s['resolution'][0];fixed=target.read_bytes()
 for label,count in [('old',1),('main',5)]:
  try:
   target.write_bytes((v/(label+'-schema.py')).read_bytes())
   record[label+'_baseline']=tests(label+'-baseline',s['regression_args'],failures=count,expected_tests=7)
  finally:target.write_bytes(fixed)
 exact()
 record['owning']=tests('owning',['tests/unittests/utils','tests/unittests/tools/test_agent_tool.py','tests/unittests/tools/test_set_model_response_tool.py','tests/unittests/flows'])
 run(['uv','tool','install','pre-commit'],c);run(['pre-commit','install'],c)
 try:
  output=run(['pre-commit','run','--files',*s['files']],c)
  record['scoped_hook_skips']=output.decode().count('Skipped')
 finally:
  delta=git('diff','--full-index','--binary')
  record['post_hook_delta_base64']=base64.b64encode(delta).decode()
  if delta:print('OCT22_ADK_FORMATTER_DELTA '+record['post_hook_delta_base64'],flush=True)
 exact()
 env={key:s['person'][value]for key,value in [('GIT_AUTHOR_NAME','name'),('GIT_AUTHOR_EMAIL','email'),('GIT_AUTHOR_DATE','date'),('GIT_COMMITTER_NAME','name'),('GIT_COMMITTER_EMAIL','email'),('GIT_COMMITTER_DATE','date')]}
 trace=proof/'commit-trace.jsonl';env['GIT_TRACE2_EVENT']=str(trace)
 try:run(['git','commit','-m',s['message']],c,extra_env=env)
 finally:
  delta=git('diff','--full-index','--binary');record['native_post_hook_delta_base64']=base64.b64encode(delta).decode()
  if delta:print('OCT22_ADK_NATIVE_FORMATTER_DELTA '+record['native_post_hook_delta_base64'],flush=True)
 exact(committed=True)
 events=[json.loads(line)for line in trace.read_text().splitlines()]
 starts=[x for x in events if x.get('event')=='child_start' and(x.get('hook_name')=='pre-commit' or any('hooks/pre-commit'in str(a)for a in x.get('argv',[])))]
 assert starts and all(any(x.get('event')=='child_exit' and x.get('sid')==a.get('sid') and x.get('child_id')==a.get('child_id') and x.get('code')==0 for x in events)for a in starts)
 head=git('rev-parse','HEAD').decode().strip();raw=git('cat-file','commit',head)
 assert git('show','--format=%P','--no-patch',head).decode().strip().split()==[s['head'],s['main']]
 native={'head':head,'tree':s['tree'],'parents':[s['head'],s['main']],'commit_raw_base64':base64.b64encode(raw).decode(),'normal_hook_starts':starts,
 'source_blobs':{p:git('rev-parse','HEAD:'+p).decode().strip()for p in s['files']}}
 record['native']=native;print('OCT22_ADK_NATIVE '+json.dumps(native),flush=True)
 run(['uv','tool','install','tox==4.65.3','--with','tox-uv==1.37.0'],c)
 run(['tox','-e',os.environ['TOX_LANE']],c)
 record['full_tox_passed']=True;exact(committed=True)
 assert sha((c/'uv.lock').read_bytes())==lock
 record['state']='passed actual merge, both regressions, owning tests, normal commit hooks and full tox lane'
except BaseException as error:
 record.update(state='failed_or_partial',error=repr(error));raise
finally:print('OCT22_ADK_RESULT '+json.dumps(record),flush=True)
