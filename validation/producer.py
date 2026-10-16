"""Public Ubuntu frozen-scope verification and native object export.

This controller runs outside the upstream checkout. It never pushes a ref,
publishes a PR, reads a secret, or changes a default branch.
"""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request

CONTROL = Path(__file__).resolve().parent
PROOFS = Path('/tmp/oct13-evening-vc-proofs')
PROOFS.mkdir(exist_ok=True)
ALLOWED = {
    'cmux': ('manaflow-ai/cmux', '590b0c7b80a750c9d4eac9037ed97093a0f30043'),
    'vllm': ('vllm-project/vllm', 'fb3fec970e1efc9b0069c81279bd82c84634c2bb'),
}
PHASES = {'cmux': {'produce'}, 'vllm': {'precheck', 'personal', 'signed'}}
record = {'started_at': datetime.now(timezone.utc).isoformat(), 'commands': [],
          'public_mutations': [], 'model_inference': False}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_receipt():
    (PROOFS / (sys.argv[1] + '-' + sys.argv[2] + '.json')).write_text(
        json.dumps(record, indent=2), encoding='utf-8')


def run(args, cwd, *, expected=0, timeout=600, env=None):
    require(args and all(isinstance(a, str) and a for a in args), 'Empty/malformed command.')
    result = subprocess.run(args, cwd=cwd, env=env or os.environ.copy(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    text = result.stdout.decode('utf-8', 'replace')
    require(len(result.stdout) <= 2*1024**2, 'One command exceeded bounded log output.')
    entry = dict(command=args, cwd=str(cwd), exit_code=result.returncode, stdout=text,
                 completed_at=datetime.now(timezone.utc).isoformat())
    record['commands'].append(entry)
    write_receipt()
    print('OCT13_COMMAND=' + json.dumps(entry), flush=True)
    require(result.returncode == expected, 'Unexpected command exit code: ' + json.dumps(args))
    return result.stdout


def verify_patch_bytes(raw, expected):
    require(raw and digest(raw) == expected, 'Frozen patch digest differs.')
    require(raw.startswith(b'diff --git ') and b'\r\n' not in raw, 'Patch is not the frozen native LF diff.')


def checker_controls():
    # Check fail-closed boundaries before using this new controller for proof.
    normal = b'diff --git a/a b/a\n'
    verify_patch_bytes(normal, digest(normal))
    failures = 0
    for raw, expected in [(normal + b'x', digest(normal)), (b'', digest(b''))]:
        try:
            verify_patch_bytes(raw, expected)
        except RuntimeError:
            failures += 1
    require(failures == 2, 'Digest checker accepted a failing control.')
    try:
        run([sys.executable, '-c', 'import sys; sys.exit(23)'], CONTROL, timeout=30)
    except RuntimeError:
        require(record['commands'][-1]['exit_code'] == 23, 'Crash control was not observed.')
    else:
        raise RuntimeError('Command checker accepted a failed child.')
    record['checker_controls_passed'] = True


def download(url, limit):
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read(limit + 1)
    require(len(raw) <= limit, 'Download exceeds controller bound.')
    return raw


def install_uv():
    tools = Path('/tmp/oct13-evening-vc-tools')
    tools.mkdir(exist_ok=True)
    url = 'https://github.com/astral-sh/uv/releases/download/0.12.23/uv-x86_64-unknown-linux-gnu.tar.gz'
    archive = download(url, 40*1024**2)
    checksum = download(url + '.sha256', 4096).decode().split()[0]
    require(digest(archive) == checksum, 'Official pinned uv checksum differs.')
    archive_path = tools / 'uv.tar.gz'
    archive_path.write_bytes(archive)
    with tarfile.open(archive_path) as tar:
        matches = [m for m in tar.getmembers() if m.isfile() and m.name.endswith('/uv')]
        require(len(matches) == 1 and matches[0].size < 100*1024**2, 'Unexpected uv archive.')
        raw = tar.extractfile(matches[0]).read()
    binary = tools / 'uv'
    binary.write_bytes(raw)
    binary.chmod(0o755)
    os.environ['PATH'] = str(tools) + ':' + os.environ['PATH']
    record['uv'] = dict(version='0.12.23', archive_sha256=checksum, binary_sha256=digest(raw), source=url)
    run(['uv', '--version'], CONTROL, timeout=30)


def source_guard(cwd, scope, *, patched):
    for file in scope['files']:
        raw = (cwd / file['path']).read_bytes()
        expected = file['patched_sha256'] if patched else file['baseline_sha256']
        require(digest(raw) == expected, 'Source differs: ' + file['path'])
        if not patched:
            blob = run(['git', 'rev-parse', scope['base'] + ':' + file['path']], cwd, timeout=30).decode().strip()
            require(blob == file['baseline_blob'], 'API baseline blob association differs.')


def current_patch(cwd, scope):
    return run(['git', 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
                scope['base'], '--', *[f['path'] for f in scope['files']]], cwd, timeout=60)


def setup_vllm(cwd):
    run(['uv', 'venv', '--python', '3.12', '.venv'], cwd)
    os.environ['VIRTUAL_ENV'] = str(cwd / '.venv')
    os.environ['PATH'] = str(cwd / '.venv/bin') + ':' + os.environ['PATH']
    if sys.argv[2] == 'signed':
        # The exact source already passed real CPU tests and the human's run.
        # This job executes normal commit hooks; it makes no fresh runtime claim.
        run(['uv', 'pip', 'install', '-r', 'requirements/lint.txt'], cwd, timeout=600)
        prefix = ['uv', 'run', '--active', '--no-project', '--python', '.venv/bin/python']
        run([*prefix, 'pre-commit', 'install'], cwd)
        return prefix
    # Upstream setup.py supports the empty target for Python-only utilities.
    # Install actual CPU runtime/build requirements first; no fake modules or
    # source edits replace unavailable precompiled native CPU artifacts.
    setup_env = {**os.environ, 'VLLM_TARGET_DEVICE': 'empty'}
    run(['uv', 'pip', 'install', '-r', 'requirements/cpu.txt', '-r',
         'requirements/build/cpu.txt', '--torch-backend', 'cpu'], cwd, timeout=1200)
    run(['uv', 'pip', 'install', '--editable', '.', '--no-build-isolation',
         '--torch-backend', 'cpu'], cwd, timeout=1200, env=setup_env)
    run(['uv', 'pip', 'install', '-r', 'requirements/lint.txt', 'pytest', 'anthropic'], cwd, timeout=600)
    prefix = ['uv', 'run', '--active', '--no-project', '--python', '.venv/bin/python']
    run([*prefix, 'pre-commit', 'install'], cwd)
    doctor = ("import json,pathlib,sys,torch; import vllm.utils.func_utils as m; "
              "expected=pathlib.Path('vllm/utils/func_utils.py').resolve(); "
              "assert pathlib.Path(m.__file__).resolve()==expected; "
              "assert torch.version.cuda is None; "
              "print(json.dumps(dict(python=sys.version,torch=torch.__version__,"
              "torch_origin=torch.__file__,func_utils_origin=m.__file__,"
              "executable=sys.executable,target='empty',native_runtime_claim=False)))")
    run([*prefix, '.venv/bin/python', '-c', doctor], cwd, timeout=90)
    return prefix


def normal_commit(cwd, scope, message, phase):
    files = [f['path'] for f in scope['files']]
    changed = [files[1]] if phase == 'red' else [files[0]] if scope['project'] == 'cmux' else files
    run(['git', 'add', '--', *changed], cwd)
    require(run(['git', 'diff', '--cached', '--name-only'], cwd).decode().splitlines() == sorted(changed), 'Unexpected staged scope.')
    run(['git', 'diff', '--cached', '--check'], cwd)
    trace = PROOFS / (scope['project'] + '-' + phase + '-hook-trace.jsonl')
    env = {**os.environ, 'GIT_AUTHOR_DATE': datetime.now(timezone.utc).isoformat(),
           'GIT_COMMITTER_DATE': datetime.now(timezone.utc).isoformat(), 'GIT_TRACE2_EVENT': str(trace)}
    run(['git', 'commit', '-m', message], cwd, env=env, timeout=900)
    entries = [json.loads(line) for line in trace.read_text().splitlines()]
    required = {'pre-commit', 'commit-msg'} if scope['project'] == 'vllm' else {'pre-commit'}
    verified = []
    for hook in required:
        starts = [e for e in entries if e.get('event') == 'child_start' and e.get('child_class') == 'hook' and e.get('hook_name') == hook]
        require(len(starts) == 1, 'Missing/repeated normal hook: ' + hook)
        start = starts[0]
        exits = [e for e in entries if e.get('event') == 'child_exit' and e.get('sid') == start['sid'] and e.get('child_id') == start['child_id']]
        require(len(exits) == 1 and exits[0]['code'] == 0, 'Normal hook did not pass: ' + hook)
        verified.append(dict(start=start, exit=exits[0]))
    require(not run(['git', 'status', '--porcelain'], cwd).strip(), 'Commit left an unexpected dirty tree.')
    head = run(['git', 'rev-parse', 'HEAD'], cwd).decode().strip()
    record.setdefault('normal_commits', []).append(dict(phase=phase, head=head, hooks=verified, trace_sha256=digest(trace.read_bytes())))
    return head


def export_native(cwd, scope):
    head = run(['git', 'rev-parse', 'HEAD'], cwd).decode().strip()
    objects = run(['git', 'rev-list', '--objects', scope['base'] + '..' + head], cwd).decode().splitlines()
    exported = []
    for row in objects:
        oid = row.split()[0]
        kind = run(['git', 'cat-file', '-t', oid], cwd).decode().strip()
        raw = run(['git', 'cat-file', kind, oid], cwd)
        require(hashlib.sha1(f'{kind} {len(raw)}\0'.encode()+raw).hexdigest() == oid, 'Native object digest differs.')
        exported.append(dict(oid=oid, type=kind, bytes=len(raw), raw_base64=base64.b64encode(raw).decode()))
    proof = dict(scope_id=scope['scope_id'], base_sha=scope['base'], base_tree=scope['baseline_root_tree'], head=head,
                 patch_sha256=scope['patch_sha256'], objects=exported, normal_commits=record['normal_commits'])
    require(len(json.dumps(proof)) < 2*1024**2, 'Native export exceeds bounded proof log.')
    print('OCT13_NATIVE_EXPORT=' + json.dumps(proof), flush=True)
    record['native_export'] = proof


def main():
    require(len(sys.argv) == 3, 'Expected project and phase.')
    project, phase = sys.argv[1:]
    require(project in PHASES and phase in PHASES[project], 'Unknown phase.')
    require(not any(os.environ.get(key) for key in ('SKIP', 'PRE_COMMIT_ALLOW_NO_CONFIG', 'GH_TOKEN', 'GITHUB_TOKEN')), 'Unexpected bypass/secret environment.')
    manifest = json.loads((CONTROL / 'manifest.json').read_text())
    scope = manifest['scopes'][project]
    scope['project'] = project
    require((scope['repo'], scope['base']) == ALLOWED[project], 'Upstream/base differs.')
    if phase == 'signed':
        approval = manifest.get('post_approval')
        require(approval and approval['patch_sha256'] == scope['patch_sha256'] and approval['dco_authorized'] is True
                and approval['review_and_user_tests_approved'] is True and len(approval['receipt_sha256']) == 64,
                'New exact personal/DCO authorization is absent.')
        require(approval.get('base_sha') == scope['base']
                and len(approval.get('body_sha256', '')) == 64,
                'The reviewed source base or frozen body binding differs.')
        precheck = approval.get('precheck_evidence', {})
        personal = approval.get('personal_evidence', {})
        require(precheck.get('repository') == 'BlueRaddish/vllm'
                and precheck.get('run_id') == 38837534576
                and precheck.get('job_id') == 116769891973
                and precheck.get('workflow_head_sha') == '94dfcd25f706250561106427c3e1cba2727aac58'
                and precheck.get('conclusion') == 'success'
                and len(precheck.get('raw_log_sha256', '')) == 64,
                'The exact-source precheck proof is absent.')
        require(personal.get('repository') == 'BlueRaddish/vllm'
                and personal.get('run_id') == 38837534576
                and type(personal.get('job_id')) is int and personal['job_id'] != 116769891664
                and type(personal.get('run_attempt')) is int and personal['run_attempt'] >= 2
                and personal.get('workflow_head_sha') == '94dfcd25f706250561106427c3e1cba2727aac58'
                and personal.get('clicked_at') and personal.get('acknowledged_at')
                and personal.get('conclusion') == 'success'
                and personal.get('test_commands') == scope['test_commands']
                and personal.get('exit_codes') == [0]
                and all(type(c) is int for c in personal['exit_codes'])
                and len(personal.get('raw_log_sha256', '')) == 64,
                'A fresh acknowledged human-run test proof is absent.')
        require(scope['patch_sha256'] == '4439585b4fc55a4f301378b23c364349ce761ae34546ea65a175c7ff0e603c94',
                'The native job must use the exact reviewed final source.')
        record['prior_tests_reused'] = dict(precheck=precheck, personal=personal,
            receipt_sha256=approval['receipt_sha256'], body_sha256=approval['body_sha256'])
        record['test_commands_executed_in_this_job'] = []
    raw_patch = (CONTROL / scope['patch_name']).read_bytes()
    verify_patch_bytes(raw_patch, scope['patch_sha256'])
    checker_controls()
    install_uv()
    cwd = Path('/tmp/oct13-evening-' + project + '-' + phase)
    require(not cwd.exists(), 'Refusing to reuse an ambiguous checkout.')
    cwd.mkdir()
    run(['git', 'init', '--initial-branch=oct13-evening-' + project], cwd)
    run(['git', 'remote', 'add', 'origin', 'https://github.com/' + scope['repo'] + '.git'], cwd)
    run(['git', 'fetch', '--depth=1', 'origin', scope['base']], cwd, timeout=300)
    run(['git', 'checkout', '--detach', 'FETCH_HEAD'], cwd)
    require(run(['git', 'rev-parse', 'HEAD'], cwd).decode().strip() == scope['base'], 'Checkout base differs.')
    require(run(['git', 'rev-parse', 'HEAD^{tree}'], cwd).decode().strip() == scope['baseline_root_tree'], 'Full upstream root tree differs.')
    require(not run(['git', 'status', '--porcelain'], cwd).strip(), 'Baseline checkout is dirty.')
    source_guard(cwd, scope, patched=False)
    run(['git', 'config', 'user.name', 'Jeeho Lee'], cwd)
    run(['git', 'config', 'user.email', 'jeeholife2@gmail.com'], cwd)
    patch = CONTROL / scope['patch_name']
    if project == 'cmux':
        env_path = Path('/tmp/oct13-evening-cmux-python')
        run(['uv', 'venv', '--python', '3.12', str(env_path)], cwd)
        run(['uv', 'pip', 'install', '--python', str(env_path / 'bin/python'), 'PyYAML'], cwd)
        os.environ['PATH'] = str(env_path / 'bin') + ':' + os.environ['PATH']
        run(['bash', 'scripts/install-git-hooks.sh'], cwd)
        installed = run(['git', 'config', '--get', 'core.hooksPath'], cwd).decode().strip()
        require(Path(installed).resolve() == (cwd / '.git/cmux-git-hooks').resolve(), 'Trusted normal cmux hooks not installed.')
        run(['python3', 'scripts/verify-local.py', '--list'], cwd)
        run(['git', 'apply', '--include=' + scope['files'][1]['path'], str(patch)], cwd)
        for args in scope['focused_commands']:
            run(args, cwd, expected=1)
        require(digest((cwd / scope['files'][0]['path']).read_bytes()) == scope['files'][0]['baseline_sha256'], 'Red regression changed production.')
        red = normal_commit(cwd, scope, 'test(ci): reproduce artifact retries beyond the transfer deadline\n\nCo-authored-by: Codex <noreply@openai.com>', 'red')
        record['red_head'] = red
        run(['git', 'apply', '--include=' + scope['files'][0]['path'], str(patch)], cwd)
        source_guard(cwd, scope, patched=True)
        require(current_patch(cwd, scope) == raw_patch, 'Applied native patch bytes differ.')
        for args in scope['focused_commands'] + scope['green_commands']:
            run(args, cwd)
        run(['python3', 'tests/test_app_host_layer_transport.py'], cwd)
        run(['python3', 'scripts/verify-local.py'], cwd, timeout=900)
        normal_commit(cwd, scope, 'fix(ci): gate artifact retry work on the remaining deadline\n\nCo-authored-by: Codex <noreply@openai.com>', 'fix')
        require(current_patch(cwd, scope) == raw_patch, 'Committed source patch bytes differ.')
        export_native(cwd, scope)
    else:
        prefix = setup_vllm(cwd)
        require(not run(['git', 'status', '--porcelain'], cwd).strip(), 'CPU setup changed tracked upstream files.')
        run(['git', 'apply', '--include=' + scope['files'][1]['path'], str(patch)], cwd)
        if phase == 'precheck':
            for args in scope['focused_commands']:
                run(args, cwd, expected=1)
            run(scope['consumer_command'], cwd, expected=1)
        run(['git', 'apply', '--include=' + scope['files'][0]['path'], str(patch)], cwd)
        source_guard(cwd, scope, patched=True)
        require(current_patch(cwd, scope) == raw_patch, 'Applied native patch bytes differ.')
        if phase not in ('personal', 'signed'):
            run([*prefix, 'pre-commit', 'run', '--files', *[f['path'] for f in scope['files']]], cwd, timeout=900)
            source_guard(cwd, scope, patched=True)
            require(current_patch(cwd, scope) == raw_patch, 'Formatter changed frozen scope; refreeze before approval.')
        commands = scope['test_commands']
        require(commands, 'Empty personal validation route.')
        if phase != 'signed':
            for args in commands:
                run(args, cwd, timeout=300)
        source_guard(cwd, scope, patched=True)
        require(current_patch(cwd, scope) == raw_patch, 'Test execution changed source.')
        if phase == 'personal':
            proof = dict(scope_id=scope['scope_id'], base_sha=scope['base'], patch_sha256=scope['patch_sha256'],
                         test_commands=commands, exit_codes=[0 for _ in commands])
            print('OCT13_USER_VERIFY=' + json.dumps(proof), flush=True)
            record['personal_proof'] = proof
        elif phase == 'signed':
            normal_commit(cwd, scope, '[Bugfix] Preserve bound method receiver semantics in keyword filters\n\nSigned-off-by: Jeeho Lee <jeeholife2@gmail.com>\nCo-authored-by: Codex <noreply@openai.com>', 'signed')
            require(current_patch(cwd, scope) == raw_patch, 'Signed commit changed reviewed scope.')
            export_native(cwd, scope)
        else:
            record['source_left_uncommitted_and_unsigned'] = True
    record['completed_at'] = datetime.now(timezone.utc).isoformat()
    write_receipt()
    # Log proof survives the runner's temporary checkout cleanup; root saves it
    # outside disposable state before source exposure or publication.
    print('OCT13_JOB_RECEIPT=' + json.dumps(record), flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        record['failure'] = type(exc).__name__ + ': ' + str(exc)
        write_receipt()
        print('OCT13_JOB_FAILURE=' + json.dumps(record), flush=True)
        raise
