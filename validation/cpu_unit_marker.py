"""Apply the upstream non-GPU-unit cleanup opt-out only to the exact parser module."""
import json
from pathlib import Path
import pytest

def pytest_collection_modifyitems(items):
    target=(Path.cwd()/'tests/utils_/test_argparse_utils.py').resolve()
    marked=[]
    for item in items:
        if item.path.resolve()==target:
            item.add_marker(pytest.mark.skip_global_cleanup)
            marked.append(item.nodeid)
    print('OCT18E_VLLM_CPU_MARKER '+json.dumps({'marker':'skip_global_cleanup',
          'exact_module':str(target),'marked_count':len(marked),'nodeids':marked}),flush=True)
