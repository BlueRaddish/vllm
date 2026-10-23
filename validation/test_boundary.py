"""Exercise the exported executor on real files at both sides of its cap."""
import pytest
from openhands.tools.gemini.list_directory import ListDirectoryAction,ListDirectoryExecutor
from openhands.tools.gemini.list_directory.definition import MAX_ENTRIES

@pytest.mark.parametrize("recursive",[False,True])
@pytest.mark.parametrize("count",[0,499,500,501])
def test_real_list_directory_boundary(tmp_path,recursive,count):
 for n in range(count):(tmp_path/(".hidden_tea_茶_"+str(n))).write_text("unchanged",encoding="utf-8")
 executor=ListDirectoryExecutor(workspace_root=str(tmp_path))
 observation=executor(ListDirectoryAction(dir_path=".",recursive=recursive))
 assert not observation.is_error
 assert len(observation.entries)==min(count,MAX_ENTRIES)
 assert observation.is_truncated is (count>MAX_ENTRIES)
 assert ("truncated to 500" in observation.text) is (count>MAX_ENTRIES)
 if count<=MAX_ENTRIES:assert observation.total_count==count
 payload=observation.model_dump(mode="json")
 assert payload["is_truncated"] is (count>MAX_ENTRIES)
 assert len(payload["entries"])==min(count,MAX_ENTRIES)
 assert all(p.read_text(encoding="utf-8")=="unchanged"for p in tmp_path.iterdir())
