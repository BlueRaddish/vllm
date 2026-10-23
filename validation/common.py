"""Run frozen native source checks; never uses an API, remote ref, or publication command."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import traceback
import xml.etree.ElementTree as ET


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def run(command, cwd, *, expect=0, extra_env=None, log_stdout=True):
    result = subprocess.run(command, cwd=cwd, env=dict(os.environ, **(extra_env or {})),
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=2700)
    item = {'command': command, 'cwd': str(cwd), 'exit_code': result.returncode,
            'stdout': result.stdout.decode(errors='replace') if log_stdout else None,
            'stdout_sha256': sha(result.stdout), 'stdout_bytes': len(result.stdout),
            'stderr': result.stderr.decode(errors='replace')}
    print('MM_COMMAND=' + json.dumps(item), flush=True)
    assert result.returncode == expect, f'Unexpected command exit {result.returncode}: {command}'
    return result.stdout


def prove(source, spec, *, fixed, head):
    assert run(['git', 'rev-parse', 'HEAD'], source).decode().strip() == head
    files = {}
    for name in spec['files']:
        raw = (source / name).read_bytes()
        expected = spec['source_proof'][name] if fixed else spec['base_source'][name]
        assert sha(raw) == expected['canonical_LF_sha256'] and git_blob(raw) == expected['target_git_blob']
        files[name] = {'sha256': sha(raw), 'git_blob': git_blob(raw)}
    actual_config = {name: sha((source / name).read_bytes()) for name in spec['config_sha256']}
    assert actual_config == spec['config_sha256'], 'Source configuration changed.'
    patch = run(['git', 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
                 spec['base'], '--', *spec['files']], source, log_stdout=False)
    assert sha(patch) == (spec['patch_sha256'] if fixed else sha(b''))
    changed = run(['git', 'diff', '--name-only', spec['base']], source).decode().splitlines()
    assert set(changed) == (set(spec['files']) if fixed else set())
    proof = {'head': head, 'source': files, 'config_sha256': actual_config,
             'patch_sha256': sha(patch), 'source_and_config_integrity': True}
    print('MM_SOURCE_PROOF=' + json.dumps(proof), flush=True)
    return proof


def junit(path, *, failures=0, tests=None):
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    failed = [case for case in cases if case.find('failure') is not None]
    assert cases and len(failed) == failures
    assert not list(root.iter('error')) and not list(root.iter('skipped'))
    if tests is not None:
        assert len(cases) == tests
    value = {'tests': len(cases), 'failures': len(failed), 'errors': 0, 'skipped': 0,
             'failed_names': [case.attrib['name'] for case in failed]}
    print('MM_JUNIT=' + json.dumps(value), flush=True)
    return value

