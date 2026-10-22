import tomllib

import tamra
from tamra import paths


def test_package_version_matches_pyproject():
    # The installer and the release tag both take the version from tamra.__version__.
    pyproject = tomllib.loads((paths.resource_dir() / "pyproject.toml").read_text("utf-8"))
    assert pyproject["project"]["version"] == tamra.__version__
