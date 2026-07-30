import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# The historical suite exercises the original all-in-one application.  Lite
# mode has a different Tally boundary and dedicated sync/transfer tests.
os.environ.setdefault("SETUORA_ALLOW_LEGACY_TEST_MODE", "true")
os.environ.setdefault("SETUORA_APP_MODE", "legacy")

from app.database import Base


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
