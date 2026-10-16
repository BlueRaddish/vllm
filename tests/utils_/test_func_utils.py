# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# ruff: noqa

import gc
import weakref

import pytest

from vllm.utils.func_utils import get_allowed_kwarg_only_overrides, supports_kw


@pytest.mark.parametrize(
    ("callable", "kw_name", "requires_kw_only", "allow_var_kwargs", "is_supported"),
    [
        # Tests for positional argument support
        (lambda foo: None, "foo", True, True, False),
        (lambda foo: None, "foo", False, True, True),
        # Tests for positional or keyword / keyword only
        (lambda foo=100: None, "foo", True, True, False),
        (lambda *, foo: None, "foo", False, True, True),
        # Tests to make sure the names of variadic params are NOT supported
        (lambda *args: None, "args", False, True, False),
        (lambda **kwargs: None, "kwargs", False, True, False),
        # Tests for if we allow var kwargs to add support
        (lambda foo: None, "something_else", False, True, False),
        (lambda foo, **kwargs: None, "something_else", False, True, True),
        (lambda foo, **kwargs: None, "kwargs", True, True, False),
        (lambda foo, **kwargs: None, "foo", True, True, False),
    ],
)
def test_supports_kw(
    callable, kw_name, requires_kw_only, allow_var_kwargs, is_supported
):
    assert (
        supports_kw(
            callable=callable,
            kw_name=kw_name,
            requires_kw_only=requires_kw_only,
            allow_var_kwargs=allow_var_kwargs,
        )
        == is_supported
    )


@pytest.mark.parametrize("allow_var_kwargs", [False, True])
def test_bound_receiver_is_not_a_keyword_override(allow_var_kwargs):
    class Processor:
        def process(self, *, size=1, **kwargs):
            return size, kwargs

        @classmethod
        def from_pretrained(cls, *, size=1, **kwargs):
            return size, kwargs

    processor = Processor()
    # Bound and unbound calls must not share the same cached signature state.
    assert supports_kw(Processor.process, "self", allow_var_kwargs=allow_var_kwargs)
    assert not supports_kw(processor.process, "self", allow_var_kwargs=allow_var_kwargs)
    assert supports_kw(Processor.process, "self", allow_var_kwargs=allow_var_kwargs)
    for factory, receiver in (
        (processor.process, "self"),
        (Processor.from_pretrained, "cls"),
    ):
        overrides = get_allowed_kwarg_only_overrides(
            factory,
            {receiver: "invalid", "size": 2},
            requires_kw_only=False,
            allow_var_kwargs=allow_var_kwargs,
        )
        assert factory(**overrides) == (2, {})


@pytest.mark.parametrize("allow_var_kwargs", [False, True])
def test_positional_only_bound_receiver_name_uses_var_kwargs(allow_var_kwargs):
    class Processor:
        def process(self, /, **kwargs):
            return kwargs

    factory = Processor().process
    overrides = get_allowed_kwarg_only_overrides(
        factory,
        {"self": "extra"},
        allow_var_kwargs=allow_var_kwargs,
    )
    assert factory(**overrides) == ({"self": "extra"} if allow_var_kwargs else {})


def test_bound_keyword_checks_do_not_retain_the_receiver():
    class Processor:
        def process(self, *, size=1):
            return size

    processor = Processor()
    reference = weakref.ref(processor)
    assert supports_kw(processor.process, "size")
    del processor
    gc.collect()
    assert reference() is None
