import pytest

from glia import fakevault


fixture = pytest.fixture


@fixture
def vault(tmp_path):
    """(origin_bare, clone) of a small Obsidian-style vault; the clone is on main."""
    return fakevault.build(tmp_path)


@fixture
def clone(vault):
    return vault[1]
