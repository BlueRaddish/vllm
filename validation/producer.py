"""Reuse the source-isolated Ubuntu SDK route; keep normal red/fix Git commits."""
from datetime import datetime,timezone
import base64,hashlib,json,os,re,shutil,subprocess,sys,tarfile,urllib.request
from pathlib import Path
C=Path(__file__).resolve().parent;S=json.loads((C/'manifest.json').read_text());P=Path('/tmp/oct18e-vc-cmux-proofs');P.mkdir()
R={'started_at':datetime.now(timezone.utc).isoformat(),'commands':[],'source_ref_mutations':[],'native_commits':[]}
def sha(b):return hashlib.sha256(b).hexdigest()
def save(): (P/'receipt.json').write_text(json.dumps(R,indent=2))
def need(x,m):
 if not x:raise RuntimeError(m)
def run(a,cwd,*,expected=0,env=None,timeout=600):
 p=subprocess.run(a,cwd=cwd,env=env or os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
 need(len(p.stdout)<4*1024**2,'command output exceeds bound')
 row={'command':a,'cwd':str(cwd),'exit_code':p.returncode,'stdout':p.stdout.decode('utf8','replace')};R['commands'].append(row);save();print('OCT18E_VC_COMMAND='+json.dumps(row),flush=True)
 need(p.returncode==expected,'unexpected exit: '+str(a));return row['stdout']
def rawgit(cwd,typ,oid):
 p=subprocess.run(['git','cat-file',typ,oid],cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
 b=p.stdout;need(len(b)<4*1024**2,'Git object exceeds bound');need(hashlib.sha1(typ.encode()+b' '+str(len(b)).encode()+b'\0'+b).hexdigest()==oid,'raw object identity differs');return b
def download(url,limit):
 with urllib.request.urlopen(url,timeout=60)as response:b=response.read(limit+1)
 need(len(b)<=limit,'download exceeds bound');return b
def setup(cwd):
 t=Path('/tmp/oct18e-vc-tools');t.mkdir();u='https://github.com/astral-sh/uv/releases/download/0.12.23/uv-x86_64-unknown-linux-gnu.tar.gz';b=download(u,40*1024**2);checksum=download(u+'.sha256',4096).decode().split()[0];need(sha(b)==checksum,'uv checksum differs');(t/'uv.tar.gz').write_bytes(b)
 with tarfile.open(t/'uv.tar.gz')as tar:
  members=[m for m in tar.getmembers()if m.isfile()and m.name.endswith('/uv')];need(len(members)==1 and members[0].size<100*1024**2,'uv archive differs');(t/'uv').write_bytes(tar.extractfile(members[0]).read());(t/'uv').chmod(0o755)
 os.environ['PATH']=str(t)+':'+os.environ['PATH'];run(['uv','--version'],cwd);v=Path('/tmp/oct18e-vc-python');run(['uv','venv','--python','3.12',str(v)],cwd);run(['uv','pip','install','--python',str(v/'bin/python'),*S['build_tools']],cwd);os.environ['PATH']=str(v/'bin')+':'+os.environ['PATH'];R['uv_archive_sha256']=checksum
 R['installed_versions']=json.loads(run(['python3','-c',"import importlib.metadata as m,json;print(json.dumps({p:m.version(p)for p in ('PyYAML','pip','setuptools','wheel')}))"],cwd))
def sources(cwd,fixed):
 need(sha((cwd/S['source']).read_bytes())==S['fixed_source_sha256'if fixed else'baseline_source_sha256'],'production bytes differ');need(sha((cwd/S['test']).read_bytes())==S['test_sha256'],'test bytes differ')
 for p,h in S['snapshots'].items():need(sha((cwd/p).read_bytes())==h,'config/policy bytes differ: '+p)
def commit(cwd,message,parent,env):
 before=run(['git','diff','--cached','--binary'],cwd);trace=P/('hook-'+str(len(R['native_commits']))+'.jsonl');msg=P/('message-'+str(len(R['native_commits']))+'.txt');msg.write_text(message)
 e={**env,'GIT_TRACE2_EVENT':str(trace),'GIT_AUTHOR_DATE':S['date'],'GIT_COMMITTER_DATE':S['date']}
 run(['git','commit','-F',str(msg)],cwd,env=e)
 events=[json.loads(x)for x in trace.read_text().splitlines()];starts=[x for x in events if x.get('event')=='child_start'and x.get('child_class')=='hook'and x.get('hook_name')=='pre-commit'];need(len(starts)==1,'normal hook not traced')
 exits=[x for x in events if x.get('event')=='child_exit'and x.get('sid')==starts[0]['sid']and x.get('child_id')==starts[0]['child_id']];need(len(exits)==1 and exits[0]['code']==0,'normal hook failed')
 oid=run(['git','rev-parse','HEAD'],cwd).strip();b=rawgit(cwd,'commit',oid);headers,msgbytes=b.split(b'\n\n',1);need(msgbytes==message.encode(),'native message differs');need(b'parent '+parent.encode()in headers.splitlines(),'native parent differs');need(run(['git','show','--format=','--binary','HEAD'],cwd)==before,'normal hook altered staged patch');need(not run(['git','status','--porcelain'],cwd).strip(),'native commit dirty')
 R['native_commits'].append({'sha':oid,'parent':parent,'raw_b64':base64.b64encode(b).decode(),'hook_start':starts[0],'hook_exit':exits[0],'trace_sha256':sha(trace.read_bytes())});save();return oid
def main():
 need(S['repo']=='manaflow-ai/cmux'and S['base']=='5da8121f9c71386221c88f74ca3256060c84b372','target differs');need(not any(os.environ.get(k)for k in('GH_TOKEN','GITHUB_TOKEN','SKIP','PRE_COMMIT_ALLOW_NO_CONFIG')),'secret/bypass environment')
 try:run([sys.executable,'-c','import sys;sys.exit(23)'],C,timeout=30)
 except RuntimeError:need(R['commands'][-1]['exit_code']==23,'crash control differs')
 else:raise RuntimeError('crash accepted')
 cwd=Path('/tmp/oct18e-vc-cmux');need(not cwd.exists(),'checkout exists');cwd.mkdir();run(['git','init','--initial-branch=oct18e-vc-stream'],cwd);run(['git','remote','add','origin','https://github.com/manaflow-ai/cmux.git'],cwd);run(['git','fetch','--depth=1','origin',S['base']],cwd,timeout=300);run(['git','checkout','--detach','FETCH_HEAD'],cwd);need(run(['git','rev-parse','HEAD'],cwd).strip()==S['base'],'base differs')
 setup(cwd);env={**os.environ,'PYTHONPATH':str(cwd/'cmux-tui/bindings/python')+':'+str(cwd/'cmux-tui/bindings/python/tests'),'PYTHONDONTWRITEBYTECODE':'1'}
 imported=run(['python3','-c','import cmux._protocol;print(cmux._protocol.__file__)'],cwd,env=env).strip();need(Path(imported).resolve()==(cwd/S['source']).resolve(),'source import differs')
 run(['git','config','user.name',S['person']['name']],cwd);run(['git','config','user.email',S['person']['email']],cwd);run(['bash','scripts/install-git-hooks.sh'],cwd)
 hook=Path(run(['git','config','--get','core.hooksPath'],cwd).strip());need(hook.resolve()==(cwd/'.git/cmux-git-hooks').resolve()and(hook/'pre-commit').is_file(),'effective hook differs')
 (cwd/S['test']).write_bytes((C/'test_resource_api.py').read_bytes());sources(cwd,False);run(['git','add','--',S['test']],cwd);run(['git','diff','--cached','--check'],cwd);red=commit(cwd,S['red_message'],S['base'],env);sources(cwd,False)
 focused=['python3','-m','unittest','-v',*['test_resource_api.ResourceApiTests.'+x for x in S['focused_names']]]
 baseline=run(focused,cwd,expected=1,env=env,timeout=45);need('Ran 3 tests'in baseline and'FAILED (failures=1, errors=5)'in baseline,'baseline counts differ')
 errorblocks=[b for b in baseline.split('\n'+'='*70+'\n')if b.startswith('ERROR: ')]
 need(len(errorblocks)==5,'baseline error blocks differ')
 reasons=set()
 for block in errorblocks:
  matched=re.search(r"^ERROR: test_stream_terminal_result_remains_available .*\(reason='([^']+)'\)",block)
  need(matched is not None,'unexpected baseline error test')
  reasons.add(matched[1]);exceptions=re.findall(r'^[A-Za-z_][\w.]*Error:.*$',block,re.M)
  need(exceptions==['cmux.errors.TimeoutError: stream did not produce an item before the deadline'],'terminal baseline error is not expected timeout')
 need(reasons=={'completed','canceled','closed','error','gap'},'specific terminal failures differ')
 need('cmux.errors.TimeoutError: stream did not produce an item before the deadline'in baseline and'test_stream_end_wakes_both_waiting_readers'in baseline and'test_stream_end_preserves_buffered_items_and_timeout_validation' in baseline,'baseline cause/control missing');R['baseline_counts']={'test_methods':3,'failed_subtests':5,'failed_methods':1,'passed_controls':1,'cause':'terminal entry consumed once; subsequent reads timeout'};save()
 (cwd/S['source']).write_bytes((C/'_protocol.py').read_bytes());sources(cwd,True);fixed=run(focused,cwd,env=env,timeout=45);need('Ran 3 tests'in fixed and'\nOK\n'in fixed,'fixed focused differs');R['fixed_focused_count']=3
 run(['git','add','--',S['source']],cwd);run(['git','diff','--cached','--check'],cwd);head=commit(cwd,S['fix_message'],red,env);sources(cwd,True)
 full=Path('/tmp/oct18e-vc-cmux-full');run(['git','worktree','add','--detach',str(full),head],cwd)
 try:
  sources(full,True);fullenv={**env,'PYTHONPATH':str(full/'cmux-tui/bindings/python')};origin=run(['python3','-c','import cmux._protocol;print(cmux._protocol.__file__)'],full,env=fullenv).strip();need(Path(origin).resolve()==(full/S['source']).resolve(),'full import differs')
  out=run(['python3','-m','unittest','discover','-s','cmux-tui/bindings/python/tests','-v'],full,env=fullenv,timeout=900);m=re.search(r'Ran (\d+) tests? in ',out);ok=re.search(r'^OK(?: \(skipped=(\d+)\))?$',out,re.M);need(m and ok and int(m[1])>3,'full SDK incomplete');R['full_sdk_counts']={'tests':int(m[1]),'skipped':int(ok[1]or 0)};run(['git','diff','--exit-code','HEAD'],full);sources(full,True)
 finally:run(['git','worktree','remove','--force',str(full)],cwd)
 run(['python3','scripts/verify-local.py','--all'],cwd,env=env,timeout=900);sources(cwd,True);need(not run(['git','status','--porcelain'],cwd).strip(),'final source dirty')
 need(run(['git','diff','--name-only',S['base'],head],cwd).splitlines()==sorted([S['source'],S['test']]),'final scope differs')
 objects=[]
 for line in run(['git','rev-list','--objects',S['base']+'..'+head],cwd).splitlines():
  oid=line.split()[0];typ=run(['git','cat-file','-t',oid],cwd).strip();b=rawgit(cwd,typ,oid);objects.append({'sha':oid,'type':typ,'content_b64':base64.b64encode(b).decode(),'sha256':sha(b)})
 R.update(state='passed focused red-green/full SDK/full static/normal native commits',base=S['base'],red=red,head=head,objects=objects,limits=S['limits'],completed_at=datetime.now(timezone.utc).isoformat());save();print('OCT18E_VC_RESULT='+json.dumps(R),flush=True)
if __name__=='__main__':
 try:main()
 except BaseException as error:R.update(state='failed or incomplete',error=repr(error));save();raise
