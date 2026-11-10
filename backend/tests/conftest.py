import io
import os
import random
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/test.db", STORAGE_DIR=f"{_tmp}/files", API_KEY="test-key",
                  RUN_INLINE="1", EMBEDDED_WORKER="0", RATE_LIMIT_PER_MIN="100000", ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
os.makedirs(f"{_tmp}/files", exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from faker import Faker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from eval.generate_dataset import degrade, make_truth, pdf_to_img, render_pdf  # noqa: E402

H = {"X-API-Key": "test-key"}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def truth():
    rng = random.Random(3)
    Faker.seed(3)
    return make_truth(rng, Faker(), n_items=3, cur="USD")


@pytest.fixture
def pdf_path(tmp_path, truth):
    p = tmp_path / "inv.pdf"
    render_pdf(truth, str(p), "A")
    return str(p)


def upload(client, name, data):
    r = client.post("/documents", headers=H, files=[("files", (name, data))])
    assert r.status_code == 202, r.text
    return r.json()["results"][0]
