"""Keep pytest temporary paths inside the workspace on this Windows setup."""

import shutil
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path():
    root = Path(__file__).parent / ".pytest-tmp" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
