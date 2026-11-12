"""Host-system provisioning: find-or-create an organisation by name, behind a shared key."""

import threading
import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import ConfigError, Settings, get_settings, validate_for_startup
from app.core.security import Role, issue_token
from app.models import AuditEvent, Organisation

KEY = "k" * 40


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "provisioning_key", KEY)


def _post(client, name, key=KEY):
    headers = {"X-POPs-Provisioning-Key": key} if key is not None else {}
    return client.post("/internal/orgs", headers=headers, json={"name": name})


def _org_count(session, name=None) -> int:
    session.expire_all()
    q = select(func.count()).select_from(Organisation)
    if name is not None:
        q = q.where(Organisation.name == name)
    return session.scalar(q)


def test_endpoint_does_not_exist_until_a_key_is_configured(client, session):
    assert get_settings().provisioning_key is None
    assert _post(client, "Acme").status_code == 404
    assert _org_count(session) == 0


def test_a_missing_or_wrong_key_is_refused(client, enabled, session):
    assert _post(client, "Acme", key=None).status_code == 401
    assert _post(client, "Acme", key="wrong").status_code == 401
    assert _post(client, "Acme", key=KEY[:-1]).status_code == 401
    assert _org_count(session) == 0


def test_creates_once_then_returns_the_same_org(client, enabled, session):
    first = _post(client, "  Acme School  ")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True and body["name"] == "Acme School"

    again = _post(client, "Acme School")
    assert again.status_code == 200 and again.json() == {**body, "created": False}

    other = _post(client, "Other School").json()
    assert other["org_id"] != body["org_id"] and other["created"] is True
    assert _org_count(session) == 2

    audits = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "organisation.provisioned")
    ).all()
    assert {a.after["name"] for a in audits} == {"Acme School", "Other School"}
    assert all(a.actor == "system:provisioning" for a in audits)


def test_a_blank_name_is_rejected(client, enabled, session):
    assert _post(client, "   ").status_code == 422
    assert _post(client, "").status_code == 422
    assert _org_count(session) == 0


def test_concurrent_requests_for_one_name_create_one_org(client, enabled, session):
    results: list[dict] = []
    errors: list[str] = []

    def call() -> None:
        r = _post(client, "Racy Org")
        if r.status_code == 200:
            results.append(r.json())
        else:
            errors.append(f"{r.status_code}: {r.text}")

    threads = [threading.Thread(target=call) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len({r["org_id"] for r in results}) == 1
    assert sum(r["created"] for r in results) == 1
    assert _org_count(session, "Racy Org") == 1


def test_a_provisioned_org_accepts_host_signed_tokens(client, enabled):
    org = _post(client, "Host Org").json()
    token = issue_token(uuid.uuid4(), uuid.UUID(org["org_id"]), Role.SUBMITTER, "svc@host.test")
    h = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/obligations",
        headers=h,
        json={
            "learner_ref": "u1",
            "type": "instalment",
            "amount_minor": 1000,
            "currency": "USD",
            "external_ref": "enr:1:1",
        },
    )
    assert r.status_code == 201, r.text


def test_production_refuses_a_short_provisioning_key(monkeypatch):
    monkeypatch.delenv("POPS_ENV")
    monkeypatch.delenv("POPS_VLM_BACKEND")
    monkeypatch.delenv("POPS_KYC_FACE_BACKEND")
    monkeypatch.delenv("POPS_PIPELINE_MODE")
    base = {"_env_file": None, "jwt_secret": "x" * 40, "clamav_host": "clamav"}
    validate_for_startup(Settings(**base))  # unset is fine: the endpoint simply stays off
    validate_for_startup(Settings(**base, provisioning_key="k" * 32))
    with pytest.raises(ConfigError) as e:
        validate_for_startup(Settings(**base, provisioning_key="short"))
    assert "POPS_PROVISIONING_KEY" in str(e.value)
