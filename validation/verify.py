"""Run exact source, genuine baseline failures, actual owning tests and normal hooks."""
import base64,json,os,sys
from pathlib import Path
from common import run,junit,sha
c=Path.cwd().resolve();v=Path(__file__).resolve().parent;s=json.loads((v/'spec.json').read_text());p=c.parent/'proof';p.mkdir()
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
record={'state':'pending','head':s['head'],'new_source_commit':False,'live_model_calls':False}
def git(*args):return run(['git',*args],c)
def exact():
 assert git('rev-parse','HEAD').decode().strip()==s['head']
 assert not git('status','--porcelain','--untracked-files=no').strip()
 for n,h in(s['source_sha256']|s['config_sha256']).items():assert sha((c/n).read_bytes())==h
def test(label,args,failures=0,count=None):
 raw=run(['uv','run','--no-sync','pytest',*args,'-q','--junitxml='+str(p/(label+'.xml'))],c,expect=1 if failures else 0)
 result=junit(p/(label+'.xml'),failures=failures,tests=count)
 if failures:
  assert b'AssertionError' in raw and b'ERROR collecting'not in raw and b'ImportError:'not in raw
 return result
try:
 exact();run(['uv','sync','--frozen','--group','dev','--python','3.13'],c);exact()
 # This is the same installed workspace package that the repository tests consume.
 code='import hashlib,sys;from pathlib import Path;import openhands.tools.gemini.list_directory.impl as m;p=Path(m.__file__).resolve();assert p==Path(sys.argv[1]).resolve();assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[2];print(sys.version);print(p)'
 run(['uv','run','--no-sync','python','-c',code,str(c/s['production']),s['source_sha256'][s['production']]],c)
 record['fixed_author']=test('fixed-author',[s['test']],count=18)
 record['fixed_independent']=test('fixed-independent',[str(v/'test_boundary.py')],count=8)
 target=c/s['production'];fixed=target.read_bytes()
 try:
  target.write_bytes((v/'baseline.py').read_bytes())
  record['baseline_author']=test('baseline-author',[s['test']],failures=4,count=18)
  record['baseline_independent']=test('baseline-independent',[str(v/'test_boundary.py')],failures=2,count=8)
 finally:target.write_bytes(fixed)
 exact();record['owning']=test('owning',['tests/tools/gemini'])
 record['callers']=git('grep','-n','ListDirectoryExecutor','--','openhands-tools','tests/tools/gemini').decode()
 run(['uv','run','--no-sync','pre-commit','install'],c)
 try:
  hook=run(['uv','run','--no-sync','pre-commit','run','--files',*s['files']],c)
  record['normal_scoped_hooks_passed']=True;record['hook_skips']=hook.decode().count('Skipped')
 finally:
  delta=git('diff','--full-index','--binary');record['post_hook_delta_base64']=base64.b64encode(delta).decode()
  if delta:print('OCT22_OH6142_FORMATTER_DELTA '+record['post_hook_delta_base64'],flush=True)
 exact();record['state']='passed actual real-file regressions, boundary probes, owning suites and normal hooks'
except BaseException as error:record.update(state='failed_or_partial',error=repr(error));raise
finally:print('OCT22_OH6142_RESULT '+json.dumps(record),flush=True)
