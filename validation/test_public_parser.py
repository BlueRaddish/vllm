"""Exercise actual FlexibleArgumentParser.parse_args for empty/type/precedence boundaries."""
from pathlib import Path
import pytest
from vllm.utils.argparse_utils import FlexibleArgumentParser

def parser():
    p = FlexibleArgumentParser()
    p.add_argument('serve')
    p.add_argument('model_tag', nargs='?')
    p.add_argument('--config')
    p.add_argument('--port', type=int, default=7777)
    p.add_argument('--tensor-parallel-size', type=int, default=1)
    p.add_argument('--trust-remote-code', action='store_true')
    return p

@pytest.mark.parametrize('document', ['', '# comment\n', '  \n\t\n'.replace('\t','  '), 'null\n', '~\n'],
                         ids=['empty', 'comment', 'whitespace', 'null', 'tilde'])
def test_empty_document_keeps_cli_and_defaults(tmp_path, document):
    config = tmp_path/'config.yaml'
    config.write_text(document)
    args = parser().parse_args(['serve', 'local-model', '--config', str(config), '--port', '8123'])
    assert args.model_tag == 'local-model' and args.port == 8123
    assert args.tensor_parallel_size == 1 and args.trust_remote_code is False

@pytest.mark.parametrize('document,kind', [('false\n','bool'), ('0\n','int'), ('[]\n','list'),
    ("''\n",'str'), ('- item\n','list'), ('scalar\n','str')],
    ids=['false', 'zero', 'empty-list', 'empty-string', 'list', 'scalar'])
def test_non_mapping_yaml_is_descriptive_value_error(tmp_path, document, kind):
    config = tmp_path/'config.yaml'
    config.write_text(document)
    with pytest.raises(ValueError, match='expected a dictionary/mapping, but got '+kind):
        parser().parse_args(['serve', 'local-model', '--config', str(config)])

@pytest.mark.parametrize('position', ['none', 'before', 'after'])
def test_mapping_and_cli_precedence_preserve_false_zero(tmp_path, position):
    config = tmp_path/'config.yaml'
    config.write_text('port: 0\ntrust-remote-code: false\ntensor-parallel-size: 2\n')
    args = ['serve', 'local-model', '--config', str(config)]
    if position == 'before':
        args[2:2] = ['--port', '4567']
    elif position == 'after':
        args += ['--port', '4567']
    actual = parser().parse_args(args)
    assert actual.model_tag == 'local-model'
    assert actual.port == (0 if position == 'none' else 4567)
    assert actual.tensor_parallel_size == 2 and actual.trust_remote_code is False
