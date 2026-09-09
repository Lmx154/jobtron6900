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


def complete_candidate_setup(services: Services) -> None:
    """Bring a candidate to capability "full" so per-application checks are reachable."""
    services.candidate.update_profile(
        name="Ada Engineer",
        email="ada@example.com",
        phone="+1-555-0100",
        location="Seal Beach, CA",
    )
    services.documents.save("master_resume", "Master", text_content="Ada resume", approved=True)


@pytest.fixture
def ready_services(services: Services) -> Services:
    """Services with candidate setup already complete."""
    complete_candidate_setup(services)
    return services
