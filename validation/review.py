"""Execute contributor tests against exact source and baseline without a source commit."""
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from common import run, junit, sha

control = Path(__file__).resolve().parent
spec = json.loads((control/'review-spec.json').read_text())
source = Path.cwd().resolve()
proof = source.parent/'proof'
proof.mkdir()
record = {'state':'pending', 'head':spec['head'], 'new_source_commits':0}

def exact():
    assert run(['git','rev-parse','HEAD'],source).decode().strip()==spec['head']
    assert run(['git','status','--porcelain','--untracked-files=no'],source)==b''
    for name,digest in (spec['source_sha256']|spec['configuration_sha256']).items():
        assert sha((source/name).read_bytes())==digest
    return {'head':spec['head'],'source_sha256':spec['source_sha256'],
            'configuration_sha256':spec['configuration_sha256']}

def check(label,args,expected=0):
    return run([str(source/'.venv/bin/python'),'-m','pytest',*args,'-q',
                '--junitxml='+str(proof/(label+'.xml'))],source,expect=expected)

try:
    record['source_before']=exact()
    run(['uv','venv','--python','3.14','.venv'],source)
    run(['uv','sync','--all-extras'],source)
    lock=(source/'uv.lock').read_bytes()
    record['generated_lock_sha256']=sha(lock)
    check('fixed',spec['regression_args'])
    record['fixed']=junit(proof/'fixed.xml',tests=19)
    target=source/spec['production'];fixed=target.read_bytes()
    baseline=(control/'baseline-read-file.py').read_bytes()
    assert sha(baseline)==spec['baseline_source_sha256']
    try:
        target.write_bytes(baseline)
        output=check('baseline',spec['regression_args'],expected=1).decode()
        record['baseline']=junit(proof/'baseline.xml',**spec['baseline_expected'])
        failures=[c for c in ET.parse(proof/'baseline.xml').iter('testcase') if c.find('failure') is not None]
        assert all('test_read_whole_file_with_equivalent_line_ranges' in c.attrib['name'] or
                   'test_read_empty_file_through_first_line' in c.attrib['name'] for c in failures)
        assert all('exceeds file length (0 lines)' in c.find('failure').text for c in failures)
        assert not any(t in output for t in ['ERROR collecting','ImportError:','ModuleNotFoundError:'])
    finally:
        target.write_bytes(fixed)
    record['source_restored']=exact()
    check('owning',spec['owning_args'])
    record['owning']=junit(proof/'owning.xml')
    assert record['owning']['tests']>=19
    run(['uv','tool','install','pre-commit'],source)
    run(['pre-commit','install'],source)
    hooks=run(['pre-commit','run','--files',*spec['files']],source).decode()
    record['hook_skips']=hooks.count('Skipped')
    record['addlicense_tool_unavailable']='addlicense not installed' in hooks
    record['normal_scoped_hooks_passed']=True
    record['source_after']=exact()
    assert sha((source/'uv.lock').read_bytes())==record['generated_lock_sha256']
    probe='import hashlib,json,sys;from pathlib import Path;import google.adk.tools.environment._read_file_tool as m;p=Path(m.__file__).resolve();assert p==Path(sys.argv[1]).resolve();assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[2];print(json.dumps({"origin":str(p),"python":sys.version}))'
    run([str(source/'.venv/bin/python'),'-c',probe,str(target),spec['source_sha256'][spec['production']]],source)
    record['state']='passed actual contributor regressions, owning suites and normal scoped hooks'
except BaseException as error:
    record.update(state='failed_or_partial',error=repr(error));raise
finally:
    print('OCT18E_ADK7585_RESULT '+json.dumps(record),flush=True)
