"""Renaming, deleting and paging conversations.

These are the operations that make a sidebar usable, and each of them is also an
authorization case: a rename or delete aimed at someone else's conversation must be
indistinguishable from one aimed at a conversation that does not exist.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.dependencies import get_llm_adapter
from app.domain.models.auth import AuthContext
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository
from app.main import create_app
from app.security.auth import require_auth_context

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")


@pytest.fixture
def client_with_actor() -> Iterator[tuple[TestClient, dict[str, AuthContext]]]:
    application = create_app(Settings(_env_file=None))
    application.state.conversation_repository = InMemoryConversationRepository()
    actor: dict[str, AuthContext] = {"current": ALICE}

    application.dependency_overrides[require_auth_context] = lambda: actor["current"]
    application.dependency_overrides[get_llm_adapter] = lambda: FakeLLMAdapter(
        [text_response("پاسخ")]
    )

    with TestClient(application) as client:
        yield client, actor


def test_rename_persists_and_shows_in_the_list(client_with_actor: tuple) -> None:
    client, _ = client_with_actor
    conversation_id = client.post("/conversations", json={}).json()["id"]

    renamed = client.patch(f"/conversations/{conversation_id}", json={"title": "بیمه‌نامه‌ها"})

    assert renamed.status_code == 200
    assert renamed.json()["title"] == "بیمه‌نامه‌ها"
    listed = client.get("/conversations").json()
    assert listed[0]["title"] == "بیمه‌نامه‌ها"


def test_a_blank_title_is_a_validation_error(client_with_actor: tuple) -> None:
    client, _ = client_with_actor
    conversation_id = client.post("/conversations", json={}).json()["id"]

    response = client.patch(f"/conversations/{conversation_id}", json={"title": "   "})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_another_users_conversation_cannot_be_renamed(client_with_actor: tuple) -> None:
    client, actor = client_with_actor
    conversation_id = client.post("/conversations", json={"title": "مال آلیس"}).json()["id"]

    actor["current"] = BOB
    response = client.patch(f"/conversations/{conversation_id}", json={"title": "دزدیده"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


def test_delete_removes_the_conversation_and_its_messages(client_with_actor: tuple) -> None:
    client, _ = client_with_actor
    conversation_id = client.post("/conversations", json={}).json()["id"]
    client.post(f"/conversations/{conversation_id}/messages", json={"content": "سلام"})

    deleted = client.delete(f"/conversations/{conversation_id}")

    assert deleted.status_code == 204
    assert client.get("/conversations").json() == []
    assert client.get(f"/conversations/{conversation_id}/messages").status_code == 404


def test_another_users_conversation_cannot_be_deleted(client_with_actor: tuple) -> None:
    client, actor = client_with_actor
    conversation_id = client.post("/conversations", json={"title": "مال آلیس"}).json()["id"]

    actor["current"] = BOB
    response = client.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 404

    actor["current"] = ALICE
    assert client.get("/conversations").json() != [], "the owner still has it"


def test_listing_pages_with_a_keyset_cursor(client_with_actor: tuple) -> None:
    client, _ = client_with_actor
    for title in ("اول", "دوم", "سوم"):
        client.post("/conversations", json={"title": title})

    first_page = client.get("/conversations", params={"limit": 2}).json()
    assert len(first_page) == 2

    cursor = first_page[-1]["updated_at"]
    second_page = client.get("/conversations", params={"limit": 2, "before": cursor}).json()

    assert len(second_page) == 1
    ids = {item["id"] for item in first_page} | {item["id"] for item in second_page}
    assert len(ids) == 3, "pages must not overlap and must not skip"
