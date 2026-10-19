"""Reuse the source-isolated Ubuntu SDK route; keep normal red/fix Git commits."""
from datetime import datetime,timezone
import base64,hashlib,json,os,re,shutil,subprocess,sys,tarfile,urllib.request
from pathlib import Path
C=Path(__file__).resolve().parent;S=json.loads((C/'manifest.json').read_text());P=Path('/tmp/oct18e-vc-cmux-static-proofs');P.mkdir()
R={'started_at':datetime.now(timezone.utc).isoformat(),'commands':[],'source_ref_mutations':[],'new_native_commits':[]}
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
def main():
 need(S['base']=='5da8121f9c71386221c88f74ca3256060c84b372','base differs')
 need(not any(os.environ.get(k)for k in('GH_TOKEN','GITHUB_TOKEN','SKIP','PRE_COMMIT_ALLOW_NO_CONFIG','TZ')),'unexpected secret, bypass or timezone override')
 cwd=Path('/tmp/oct18e-vc-cmux-pristine-static');need(not cwd.exists(),'checkout exists');cwd.mkdir();run(['git','init','--initial-branch=oct18e-static'],cwd);run(['git','remote','add','origin','https://github.com/manaflow-ai/cmux.git'],cwd);run(['git','fetch','--depth=1','origin',S['base']],cwd,timeout=300);run(['git','checkout','--detach','FETCH_HEAD'],cwd);need(run(['git','rev-parse','HEAD'],cwd).strip()==S['base'],'base differs')
 setup(cwd);env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
 R['actual_calendar']=run(['python3','-c',"import datetime,json,os;print(json.dumps({'local_today':str(datetime.date.today()),'utc_today':str(datetime.datetime.now(datetime.timezone.utc).date()),'TZ':os.environ.get('TZ')}))"],cwd,env=env).strip()
 def guard():
  need(sha((cwd/S['source']).read_bytes())==S['baseline_source_sha256']and sha((cwd/S['test']).read_bytes())==S['baseline_test_sha256'],'pristine production/test differs')
  for p,h in S['snapshots'].items():need(sha((cwd/p).read_bytes())==h,'pristine source/config differs: '+p)
  need(not run(['git','status','--porcelain'],cwd).strip(),'pristine source dirty')
 guard();output=run(['python3','scripts/verify-local.py','--all'],cwd,expected=1,env=env,timeout=900)
 outcomes=re.findall(r'^(PASSED|FAILED) ([a-z-]+) \(',output,re.M)
 need(len(outcomes)==16 and sum(s=='PASSED'for s,n in outcomes)==15 and[(s,n)for s,n in outcomes if s=='FAILED']==[('FAILED','feature-flags')],'pristine static failure attribution differs')
 diagnostics=[l.strip()for l in output.splitlines()if l.strip().startswith('- ')]
 need(len(diagnostics)==1 and"'conversation-sidebar-release' reviewBy 2026-10-18 has passed"in diagnostics[0],'unexpected pristine baseline failure')
 need('15 tests executed, 1 skipped'in output,'normalizer skip accounting differs');guard()
 R.update(state='incomplete: pristine and fixed full static fail the same expired unrelated flag; draft only',base=S['base'],outcomes=outcomes,diagnostics=diagnostics,passed_static_checks=15,failed_static_checks=1,internal_normalizer_skipped=1,source_unchanged=True,source_ref_mutations=[],new_native_commits=[],limits=['Pristine Linux static attribution only; no SDK rerun, source fix, native commit, flag/date edit, waiver or green verification claim.'],completed_at=datetime.now(timezone.utc).isoformat());save();print('OCT18E_VC_STATIC_ATTRIBUTION='+json.dumps(R),flush=True)
if __name__=='__main__':
 try:main()
 except BaseException as error:R.update(state='failed or incomplete; attribution not established',error=repr(error));save();raise
 raise SystemExit(1)
