"""Independently validate an existing contributor's SDK PR; no source writes."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

CONTROL = Path(__file__).resolve().parent
PROOFS = Path('/tmp/oct17e-vc-cmux21351-proofs')
PROOFS.mkdir(exist_ok=True)
SPEC = json.loads((CONTROL / 'manifest.json').read_text())
record = {'started_at': datetime.now(timezone.utc).isoformat(), 'commands': [],
          'source_ref_mutations': [], 'new_source_commits': [], 'review_event': 'none'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save():
    (PROOFS / 'receipt.json').write_text(json.dumps(record, indent=2))


def run(args, cwd, *, expected=0, timeout=600, env=None):
    result = subprocess.run(args, cwd=cwd, env=env or os.environ.copy(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    require(len(result.stdout) <= 2 * 1024**2, 'Command log exceeds bound.')
    row = {'command': args, 'cwd': str(cwd), 'exit_code': result.returncode,
           'stdout': result.stdout.decode('utf-8', 'replace')}
    record['commands'].append(row)
    save()
    print('OCT17E_VC_COMMAND=' + json.dumps(row), flush=True)
    require(result.returncode == expected, 'Unexpected exit code: ' + json.dumps(args))
    return row['stdout']


def fetch(url, limit):
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read(limit + 1)
    require(len(raw) <= limit, 'Download exceeds bound.')
    return raw


def setup(cwd):
    tools = Path('/tmp/oct17e-vc-tools')
    tools.mkdir(exist_ok=True)
    url = 'https://github.com/astral-sh/uv/releases/download/0.12.23/uv-x86_64-unknown-linux-gnu.tar.gz'
    archive = fetch(url, 40 * 1024**2)
    checksum = fetch(url + '.sha256', 4096).decode().split()[0]
    require(sha(archive) == checksum, 'Official pinned uv checksum differs.')
    archive_path = tools / 'uv.tar.gz'
    archive_path.write_bytes(archive)
    with tarfile.open(archive_path) as tar:
        rows = [m for m in tar.getmembers() if m.isfile() and m.name.endswith('/uv')]
        require(len(rows) == 1 and rows[0].size < 100 * 1024**2, 'Unexpected uv archive.')
        binary = tools / 'uv'
        binary.write_bytes(tar.extractfile(rows[0]).read())
        binary.chmod(0o755)
    os.environ['PATH'] = str(tools) + ':' + os.environ['PATH']
    run(['uv', '--version'], cwd)
    env_path = Path('/tmp/oct17e-vc-python')
    run(['uv', 'venv', '--python', '3.12', str(env_path)], cwd)
    run(['uv', 'pip', 'install', '--python', str(env_path / 'bin/python'), *SPEC['build_tools']], cwd)
    os.environ['PATH'] = str(env_path / 'bin') + ':' + os.environ['PATH']
    record['uv_archive_sha256'] = checksum
    run(['python3', '--version'], cwd)
    record['installed_versions'] = json.loads(run(['python3', '-c',
        "import importlib.metadata as m,json; print(json.dumps({p:m.version(p) for p in ('PyYAML','pip','setuptools','wheel')}))"], cwd))


def guard(cwd):
    require(run(['git', 'rev-parse', 'HEAD'], cwd).strip() == SPEC['pr_head'], 'PR head differs.')
    require(run(['git', 'show', '-s', '--format=%P', 'HEAD'], cwd).split() == [SPEC['red']],
            'Fixed commit parent differs.')
    require(run(['git', 'show', '-s', '--format=%P', SPEC['red']], cwd).split() == [SPEC['base']],
            'Regression commit parent differs.')
    require(run(['git', 'rev-parse', 'HEAD^{tree}'], cwd).strip() == SPEC['head_tree'], 'Head tree differs.')
    for row in SPEC['files']:
        require(sha((cwd / row['path']).read_bytes()) == row['sha256'], 'Frozen source differs: ' + row['path'])
        require(run(['git', 'rev-parse', 'HEAD:' + row.get('git_path', row['path'])], cwd).strip() == row['blob'],
                'Frozen source/blob association differs.')
    require(not run(['git', 'status', '--porcelain'], cwd).strip(), 'PR checkout is dirty.')


def main():
    require(SPEC['repo'] == 'manaflow-ai/cmux' and SPEC['pull_number'] == 21351, 'Unexpected target.')
    require(not any(os.environ.get(k) for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'SKIP', 'PRE_COMMIT_ALLOW_NO_CONFIG')),
            'Unexpected secret or bypass environment.')
    try:
        run([sys.executable, '-c', 'import sys;sys.exit(23)'], CONTROL, timeout=30)
    except RuntimeError:
        require(record['commands'][-1]['exit_code'] == 23, 'Crash control missing.')
    else:
        raise RuntimeError('Failed child was accepted.')
    cwd = Path('/tmp/oct17e-vc-cmux21351')
    require(not cwd.exists(), 'Isolated checkout already exists.')
    cwd.mkdir()
    run(['git', 'init', '--initial-branch=oct17e-vc-review'], cwd)
    run(['git', 'remote', 'add', 'origin', 'https://github.com/manaflow-ai/cmux.git'], cwd)
    run(['git', 'fetch', '--depth=3', 'origin', SPEC['pr_head']], cwd, timeout=300)
    require(run(['git', 'rev-parse', 'FETCH_HEAD'], cwd).strip() == SPEC['pr_head'], 'Immutable fetch differs.')
    run(['git', 'checkout', '--detach', 'FETCH_HEAD'], cwd)
    guard(cwd)
    setup(cwd)
    env = {**os.environ, 'PYTHONPATH': str(cwd / 'cmux-tui/bindings/python'), 'PYTHONDONTWRITEBYTECODE': '1'}
    run(['python3', '-c', 'import cmux.aio,sys; print(sys.version); print(cmux.aio.__file__)'], cwd, env=env)
    require(run(['git', 'diff', SPEC['base'], SPEC['pr_head'], '--name-only'], cwd).splitlines() == sorted(SPEC['patch_files']),
            'Reviewed PR scope differs.')
    # The contributor's existing regression-only commit is the focused pilot.
    # Nothing is committed, rewritten or pushed by this validation.
    run(['git', 'checkout', '--detach', SPEC['red']], cwd)
    baseline = run(SPEC['focused_command'], cwd, expected=1, env=env)
    require(re.search(r'Ran 5 tests? in ', baseline) is not None
            and 'FAILED (failures=3)' in baseline
            and 'cannot schedule new futures after shutdown' in baseline
            and 'test_caller_cancellation_is_deferred_until_cleanup_finishes' in baseline
            and 'test_wrapped_cleanup_cancellation_is_not_retried' in baseline,
            'Focused baseline did not establish exactly three behavioral failures and two cleanup controls.')
    record['baseline_counts'] = {'run': 5, 'failures': 3, 'errors': 0, 'passed': 2}
    run(['git', 'checkout', '--detach', SPEC['pr_head']], cwd)
    guard(cwd)
    fixed = run(SPEC['focused_command'], cwd, env=env)
    require(re.search(r'Ran 5 tests? in ', fixed) is not None and '\nOK\n' in fixed,
            'Fixed pilot did not execute five passing tests.')
    record['fixed_focused_counts'] = {'run': 5, 'passed': 5}
    # Only broaden after the same real focused command goes red then green.
    full_cwd = Path('/tmp/oct17e-vc-cmux21351-full-sdk')
    run(['git', 'worktree', 'add', '--detach', str(full_cwd), SPEC['pr_head']], cwd)
    try:
        guard(full_cwd)
        full_env = {**env, 'PYTHONPATH': str(full_cwd / 'cmux-tui/bindings/python')}
        full_import = run(['python3', '-c', 'import cmux.aio; print(cmux.aio.__file__)'], full_cwd, env=full_env).strip()
        require(Path(full_import).resolve() == (full_cwd / 'cmux-tui/bindings/python/cmux/aio.py').resolve(), 'Full SDK source import differs.')
        full = run(SPEC['full_command'], full_cwd, env=full_env, timeout=900)
        run(['git', 'diff', '--exit-code', 'HEAD'], full_cwd)
        record['full_sdk_post_status'] = run(['git', 'status', '--porcelain'], full_cwd)
        for row in SPEC['files']:
            require(sha((full_cwd / row['path']).read_bytes()) == row['sha256'],
                    'SDK test changed a frozen input: ' + row['path'])
    finally:
        # The package consumer may create build/egg-info only in this owned,
        # disposable exact-head worktree. Actual command output remains in PROOFS.
        run(['git', 'worktree', 'remove', '--force', str(full_cwd)], cwd)
    count = re.search(r'Ran (\d+) tests? in ', full)
    require(count is not None and int(count[1]) >= 5 and re.search(r'^OK(?: \(skipped=(\d+)\))?$', full, re.M) is not None,
            'Full SDK route did not report completed tests.')
    record['full_sdk_count'] = int(count[1])
    skipped = re.search(r'^OK \(skipped=(\d+)\)$', full, re.M)
    record['full_sdk_skipped'] = int(skipped[1]) if skipped else 0
    run(['bash', 'scripts/install-git-hooks.sh'], cwd)
    hooks = Path(run(['git', 'config', '--get', 'core.hooksPath'], cwd).strip())
    effective = (hooks if hooks.is_absolute() else cwd / hooks).resolve()
    require(effective == (cwd / '.git/cmux-git-hooks').resolve(), 'Normal trusted hooks not installed.')
    require((effective / 'pre-commit').is_file() and os.access(effective / 'pre-commit', os.X_OK),
            'Normal pre-commit is not executable.')
    hook_cwd = Path('/tmp/oct17e-vc-cmux21351-hook-scope')
    run(['git', 'worktree', 'add', '--detach', str(hook_cwd), SPEC['base']], cwd)
    try:
        for path in SPEC['patch_files']:
            shutil.copyfile(cwd / path, hook_cwd / path)
        run(['git', 'add', '--', *SPEC['patch_files']], hook_cwd)
        require(run(['git', 'diff', '--cached', '--name-only'], hook_cwd).splitlines() == sorted(SPEC['patch_files']),
                'Hook staged scope differs.')
        run(['git', 'diff', '--cached', '--check'], hook_cwd)
        trace = PROOFS / 'pre-commit.trace.jsonl'
        run(['git', 'hook', 'run', 'pre-commit'], hook_cwd,
            env={**env, 'GIT_TRACE2_EVENT': str(trace)})
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        starts = [e for e in events if e.get('event') == 'child_start' and e.get('child_class') == 'hook'
                  and e.get('hook_name') == 'pre-commit']
        require(len(starts) == 1, 'Exactly one normal pre-commit was not traced.')
        exits = [e for e in events if e.get('event') == 'child_exit' and e.get('sid') == starts[0]['sid']
                 and e.get('child_id') == starts[0]['child_id']]
        require(len(exits) == 1 and exits[0]['code'] == 0, 'Normal pre-commit did not pass.')
        record['normal_hook_trace'] = {'start': starts[0], 'exit': exits[0], 'trace_sha256': sha(trace.read_bytes())}
        for row in SPEC['files'][:2]:
            require(run(['git', 'rev-parse', ':' + row['path']], hook_cwd).strip() == row['blob']
                    and sha((hook_cwd / row['path']).read_bytes()) == row['sha256'],
                    'Normal hook changed the reviewed source bytes.')
    finally:
        run(['git', 'worktree', 'remove', '--force', str(hook_cwd)], cwd)
    run(['python3', 'scripts/verify-local.py', '--all'], cwd, env=env, timeout=900)
    guard(cwd)
    record.update(state='passed independent existing PR validation', pr_head=SPEC['pr_head'], red=SPEC['red'],
                  base=SPEC['base'], limits=SPEC['limits'], completed_at=datetime.now(timezone.utc).isoformat())
    save()
    print('OCT17E_VC_RESULT=' + json.dumps(record), flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        record.update(state='failed or incomplete; inspect actual commands', error=repr(error))
        save()
        raise
