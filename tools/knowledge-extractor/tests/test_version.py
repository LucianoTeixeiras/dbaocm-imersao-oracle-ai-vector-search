"""O __version__ do pacote acompanha o pyproject (a tag vX.Y.Z do template)."""

import tomllib
from pathlib import Path

import knowledge_extractor


def test_version_igual_ao_pyproject():
    py = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert (
        knowledge_extractor.__version__
        == tomllib.loads(py.read_text("utf-8"))["project"]["version"]
    )
