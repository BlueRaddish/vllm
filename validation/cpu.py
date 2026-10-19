"""Proven pinned-uv, actual CPU empty-target installation; no stubs or inference."""
from datetime import datetime, timezone
import hashlib, json, os, subprocess, sys, tarfile, urllib.request
from pathlib import Path
CONTROL = Path(__file__).resolve().parent
PROOFS = Path('/tmp/oct18e-ao-vllm60319-proof')
PROOFS.mkdir(exist_ok=False)
record = {'started_at': datetime.now(timezone.utc).isoformat(), 'commands': [],
          'public_mutations': [], 'model_inference': False}
def write_receipt():
    (PROOFS/'cpu-commands.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

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

def setup_vllm(cwd):
    run(['uv', 'venv', '--python', '3.12', '.venv'], cwd)
    os.environ['VIRTUAL_ENV'] = str(cwd / '.venv')
    os.environ['PATH'] = str(cwd / '.venv/bin') + ':' + os.environ['PATH']
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
    doctor = ("import json,pathlib,sys,torch; import vllm.utils.argparse_utils as m; "
              "expected=pathlib.Path('vllm/utils/argparse_utils.py').resolve(); "
              "assert pathlib.Path(m.__file__).resolve()==expected; "
              "assert torch.version.cuda is None; "
              "print(json.dumps(dict(python=sys.version,torch=torch.__version__,"
              "torch_origin=torch.__file__,argparse_utils_origin=m.__file__,"
              "executable=sys.executable,target='empty',native_runtime_claim=False)))")
    run([*prefix, '.venv/bin/python', '-c', doctor], cwd, timeout=90)
    return prefix
