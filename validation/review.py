"""Validate exact outside-author parser source; never commit or publish source."""
import json, os, sys
from pathlib import Path
import xml.etree.ElementTree as ET
from cpu import CONTROL, PROOFS, record, require, digest, run, install_uv, setup_vllm, write_receipt

spec = json.loads((CONTROL/'review-spec.json').read_text())
cwd = Path.cwd().resolve()
result = {'state':'pending', 'head':spec['head'], 'source_commits_created':0,
          'model_inference':False, 'native_runtime_claim':False}

def exact():
    require(run(['git','rev-parse','HEAD'],cwd).decode().strip()==spec['head'], 'Unexpected checkout HEAD')
    require(not run(['git','status','--porcelain','--untracked-files=no'],cwd).strip(), 'Tracked checkout is dirty')
    for path, wanted in (spec['source_sha256']|spec['configuration_sha256']).items():
        require(digest((cwd/path).read_bytes())==wanted, 'Frozen source/config differs: '+path)
    return {'head':spec['head'], 'source_sha256':spec['source_sha256'], 'configuration_sha256':spec['configuration_sha256']}

def junit(path, *, tests=None, failures=0):
    cases = list(ET.parse(path).iter('testcase'))
    counts = {'tests':len(cases), 'failures':sum(c.find('failure') is not None for c in cases),
              'errors':sum(c.find('error') is not None for c in cases),
              'skipped':sum(c.find('skipped') is not None for c in cases)}
    require(counts['tests']>0 and (tests is None or counts['tests']==tests), 'Wrong/empty JUnit test count')
    require(counts['failures']==failures and counts['errors']==counts['skipped']==0, 'Unexpected JUnit outcome')
    counts['case_names']=[c.attrib['name'] for c in cases]
    return counts

def pytest(label, args, expected=0):
    output=run([*prefix,str(cwd/'.venv/bin/python'),'-m','pytest',*args,'-q',
                '--junitxml='+str(PROOFS/(label+'.xml'))],cwd,expected=expected,timeout=600).decode()
    require(not any(x in output for x in ['ERROR collecting','ImportError:','ModuleNotFoundError:']), 'Import/collection failure is not regression evidence')
    return output

try:
    require(not any(os.environ.get(x) for x in ('SKIP','PRE_COMMIT_ALLOW_NO_CONFIG','GH_TOKEN','GITHUB_TOKEN')), 'Unexpected bypass/secret environment')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', VLLM_NO_USAGE_STATS='1', DO_NOT_TRACK='1', CUDA_VISIBLE_DEVICES='')
    result['source_before']=exact()
    require(run(['git','rev-parse','HEAD^'],cwd).decode().strip()==spec['parent'], 'Actual PR parent differs')
    callers=run(['git','grep','-n','load_config_file','--','*.py'],cwd).decode()
    result['tracked_load_config_callers']=callers
    install_uv()
    prefix=setup_vllm(cwd)
    run(['uv','pip','install',*spec['declared_test_dependencies'],'--torch-backend','cpu'],cwd,timeout=600)
    result['source_after_setup']=exact()
    pytest('public-controls',[str(CONTROL/'test_public_parser.py')])
    result['public_controls']=junit(PROOFS/'public-controls.xml',tests=14)
    regression=[spec['test_module']+'::'+name for name in spec['regression_names']+spec['control_names']]
    target=cwd/spec['production']; fixed=target.read_bytes()
    baseline=(CONTROL/'baseline-argparse.py').read_bytes()
    require(digest(baseline)==spec['baseline_source_sha256'], 'Baseline payload differs')
    require(run(['git','show',spec['parent']+':'+spec['production']],cwd)==baseline, 'Actual parent baseline differs')
    try:
        target.write_bytes(baseline)
        pytest('baseline',regression,expected=1)
        result['baseline']=junit(PROOFS/'baseline.xml',tests=5,failures=2)
        failures=[c for c in ET.parse(PROOFS/'baseline.xml').iter('testcase') if c.find('failure') is not None]
        require({c.attrib['name'] for c in failures}==set(spec['regression_names']), 'Different baseline cases failed')
        for c in failures:
            text=c.find('failure').text or ''
            require('AttributeError' in text and 'items' in text and 'argparse_utils.py' in text, 'Baseline failure is not the actual mapping iteration defect')
            require(('NoneType' if c.attrib['name']=='test_empty_config_file' else 'list') in text, 'Wrong YAML baseline failure type')
        result['baseline_failed_cases']={c.attrib['name']:c.find('failure').text for c in failures}
    finally:
        target.write_bytes(fixed)
    result['source_restored']=exact()
    pytest('fixed',regression)
    result['fixed']=junit(PROOFS/'fixed.xml',tests=5)
    output=pytest('owning',[spec['test_module'],'-k','not '+spec['network_test_excluded']])
    require('1 deselected' in output, 'Unexpected tokenizer-network deselection count')
    result['owning']=junit(PROOFS/'owning.xml')
    result['network_tests_deselected']=1
    hooks_before=exact()
    try:
        hooks=run([*prefix,str(cwd/'.venv/bin/python'),'-m','pre_commit','run','--files',*spec['files']],cwd,timeout=1200).decode()
    finally:
        difference=run(['git','diff','--binary','--full-index','--no-ext-diff','--no-textconv'],cwd)
        result['post_hook_tracked_diff']=difference.decode()
        result['post_hook_tracked_diff_sha256']=digest(difference)
        result['post_hook_source_sha256']={p:digest((cwd/p).read_bytes()) for p in spec['source_sha256']|spec['configuration_sha256']}
    result['normal_scoped_hooks_passed']=True
    result['hook_skips']=hooks.count('Skipped')
    result['source_after']=exact()
    result['state']='passed exact CPU parser regressions, public controls, owning module and normal scoped hooks'
except BaseException as error:
    result.update(state='failed_or_partial',error=repr(error))
    raise
finally:
    result['command_receipt_sha256']=digest((PROOFS/'cpu-commands.json').read_bytes()) if (PROOFS/'cpu-commands.json').exists() else None
    print('OCT18E_VLLM60319_RESULT '+json.dumps(result),flush=True)
