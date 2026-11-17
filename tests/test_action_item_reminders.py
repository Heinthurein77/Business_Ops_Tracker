"""Action-item due/overdue reminders (run_remind_action_items).

Mirrors the document-expiry pattern: open items (pending/in_progress)
with a due date of today or earlier notify their assignee once per 7
days. Done items, items without a due date, unassigned items, and items
assigned to inactive users never notify.
"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import ActionItem, Base, BusinessUnit, Notification, User
from src.routers.ops import run_remind_action_items
from src.utils import today_local, utcnow


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
        User(id=1, username="chairman", hashed_password="x", role="Chairman",
             business_unit_id=None, is_active=True),
        User(id=10, username="ceo_one", hashed_password="x", role="CEO",
             business_unit_id=1, is_active=True),
        User(id=11, username="ceo_gone", hashed_password="x", role="CEO",
             business_unit_id=1, is_active=False),
    ])
    session.commit()
    yield session
    session.close()


def _item(db, title, due, status="pending", assignee=10, bu=1):
    item = ActionItem(business_unit_id=bu, title=title, assigned_to_id=assignee,
                      due_date=due, status=status)
    db.add(item)
    db.commit()
    return item


def _notes(db, user_id=10):
    return db.query(Notification).filter(Notification.user_id == user_id).all()


def test_due_today_notifies_assignee(db):
    today = today_local()
    _item(db, "Submit audit pack", today)
    out = run_remind_action_items(db)
    assert out == {"checked": 1, "notified": 1}
    notes = _notes(db)
    assert len(notes) == 1
    assert notes[0].type == "action_due"
    assert "due today" in notes[0].title
    assert "Submit audit pack" in notes[0].title
    assert notes[0].link == "/admin/daily-ops"
    assert notes[0].is_read is False


def test_overdue_notifies_with_day_count(db):
    today = today_local()
    _item(db, "Fix stock count", today - timedelta(days=3), status="in_progress")
    out = run_remind_action_items(db)
    assert out == {"checked": 1, "notified": 1}
    assert "overdue" in _notes(db)[0].title
    assert "3 days overdue" in _notes(db)[0].message


def test_future_due_date_does_not_notify(db):
    _item(db, "Plan next quarter", today_local() + timedelta(days=2))
    assert run_remind_action_items(db) == {"checked": 0, "notified": 0}
    assert _notes(db) == []


def test_done_item_does_not_notify(db):
    _item(db, "Done task", today_local(), status="done")
    assert run_remind_action_items(db) == {"checked": 0, "notified": 0}


def test_no_due_date_does_not_notify(db):
    _item(db, "No deadline", None)
    assert run_remind_action_items(db) == {"checked": 0, "notified": 0}


def test_unassigned_item_does_not_notify(db):
    _item(db, "Nobody owns this", today_local(), assignee=None)
    # Unassigned items are not even checked: nobody to notify.
    assert run_remind_action_items(db) == {"checked": 0, "notified": 0}


def test_inactive_assignee_does_not_notify(db):
    _item(db, "Left the company", today_local(), assignee=11)
    assert run_remind_action_items(db) == {"checked": 1, "notified": 0}
    assert _notes(db, user_id=11) == []


def test_throttled_to_one_reminder_per_7_days(db):
    today = today_local()
    item = _item(db, "Weekly nudge", today - timedelta(days=1))
    assert run_remind_action_items(db)["notified"] == 1
    assert run_remind_action_items(db) == {"checked": 1, "notified": 0}
    assert len(_notes(db)) == 1
    # 8 days later the throttle has expired: it reminds again.
    item.reminder_sent_at = utcnow() - timedelta(days=8)
    db.commit()
    assert run_remind_action_items(db)["notified"] == 1
    assert len(_notes(db)) == 2


def test_multiple_items_each_notify_once(db):
    today = today_local()
    _item(db, "First", today)
    _item(db, "Second", today - timedelta(days=5))
    out = run_remind_action_items(db)
    assert out == {"checked": 2, "notified": 2}
    titles = [n.title for n in _notes(db)]
    assert any("First" in t for t in titles)
    assert any("Second" in t for t in titles)
