import pytest

from app.config import Settings
from app.db import Database
from app.service import Service

from .helpers import STAFF, FakeCalendar, FakeClock, FakeWhatsApp


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / "test.db"),
        staff_phones=list(STAFF),
        panel_users={"laura": "clave-laura", "asistente": "clave-asistente"},
    )


@pytest.fixture
def clock():
    return FakeClock("2026-11-02 12:00")


@pytest.fixture
def wa():
    return FakeWhatsApp()


@pytest.fixture
def cal():
    return FakeCalendar()


@pytest.fixture
def service(settings, clock, wa, cal):
    return Service(settings, Database(settings.db_path), wa, cal, clock)
