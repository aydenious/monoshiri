"""Pins how comment threads are rendered for the viewing user."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.chat_comments_service import _author_response, _comment_response

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _user(**over):
    base = {
        "id": uuid4(),
        "display_name": "Ann",
        "avatar_url": "http://a/x.png",
        "email": "ann@example.com",
    }
    return SimpleNamespace(**{**base, **over})


def _comment(*, id, author, created=_T0, edited=False, replies=()):
    return SimpleNamespace(
        id=id,
        message_id=9,
        content="hello",
        author=author,
        author_id=author.id if author else None,
        created_at=created,
        updated_at=created + timedelta(minutes=1 if edited else 0),
        replies=list(replies),
    )


def test_author_response_copies_every_field_and_keeps_none_fields():
    user = _user(display_name=None, avatar_url=None)

    response = _author_response(user)

    assert response.model_dump() == {
        "id": user.id,
        "display_name": None,
        "avatar_url": None,
        "email": "ann@example.com",
    }
    assert _author_response(None) is None


def test_own_comment_gets_edit_and_delete_without_delete_any():
    me = _user()
    comment = _comment(id=1, author=me, edited=True)

    response = _comment_response(comment, me, {}, can_delete_any=False)

    assert (response.id, response.message_id, response.content) == (1, 9, "hello")
    assert response.content_rendered == "hello"
    assert response.author.id == me.id
    assert (response.created_at, response.updated_at) == (
        comment.created_at,
        comment.updated_at,
    )
    assert response.is_edited is True
    assert (response.can_edit, response.can_delete) == (True, True)
    assert response.reply_count == 0 and response.replies == []


def test_others_comment_and_replies_follow_ownership_and_delete_any():
    me, other = _user(), _user()
    late = _comment(id=3, author=other, created=_T0 + timedelta(hours=2), edited=True)
    mine = _comment(id=2, author=me)
    orphan = _comment(id=4, author=None, created=_T0 + timedelta(hours=3))
    top = _comment(id=1, author=other, replies=[orphan, late, mine])

    plain = _comment_response(top, me, {}, can_delete_any=False)
    admin = _comment_response(top, me, {}, can_delete_any=True)

    assert [r.id for r in plain.replies] == [2, 3, 4]
    assert plain.reply_count == 3
    assert (plain.can_edit, plain.can_delete) == (False, False)
    assert (admin.can_edit, admin.can_delete) == (False, True)
    assert [(r.can_edit, r.can_delete) for r in plain.replies] == [
        (True, True),
        (False, False),
        (False, False),
    ]
    assert [r.can_delete for r in admin.replies] == [True, True, True]
    assert [r.is_edited for r in plain.replies] == [False, True, False]
    assert plain.replies[2].author is None
