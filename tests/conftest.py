from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.services import Services
from app.storage import FileStorage


@pytest.fixture
def services(tmp_path: Path) -> Services:
    db=Database(tmp_path/"app.db"); db.initialize(); return Services(db,FileStorage(tmp_path/"files"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings: return Settings(tmp_path)
