import warnings
from datetime import date

import pytest

from rulegraph import Settings

from madhava import build

warnings.filterwarnings("ignore")

TODAY = date(2026, 10, 14)


@pytest.fixture()
def system(tmp_path):
    return build(Settings(today=TODAY, episodic_dir=tmp_path / "episodic"))
