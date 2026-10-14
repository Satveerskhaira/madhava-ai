import warnings
from datetime import date

import pytest

from madhava.app import build_system
from madhava.knowledge import Knowledge
from madhava.settings import Settings

warnings.filterwarnings("ignore")

TODAY = date(2026, 10, 14)


@pytest.fixture(scope="session")
def knowledge() -> Knowledge:
    return Knowledge.load(Settings(today=TODAY))


@pytest.fixture()
def system(knowledge, tmp_path):
    settings = Settings(today=TODAY, episodic_dir=tmp_path / "episodic")
    return build_system(settings, knowledge=knowledge)
