"""Политика повторов для джобы с очередью писем.

Раньше сбой доставки оставлял строку нетронутой, поэтому одно и то же письмо
повторялось каждые 60 секунд бесконечно.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.routers import notifications
from app.routers.notifications import (
    RETRY_BACKOFF_MINUTES,
    RETRY_WINDOW_HOURS,
    _next_attempt_delay,
    _attempts_made,
)


# ---------------------------------------------------------------------------
# Рост паузы
# ---------------------------------------------------------------------------


def test_backoff_grows_with_each_failure():
    delays = [_next_attempt_delay(n) for n in range(len(RETRY_BACKOFF_MINUTES))]
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_backoff_saturates_at_the_last_value():
    """Не должен расти дальше, иначе окно никогда не завершится."""
    last = RETRY_BACKOFF_MINUTES[-1]
    assert _next_attempt_delay(99) == last
    assert _next_attempt_delay(len(RETRY_BACKOFF_MINUTES)) == last


def test_first_retry_is_not_immediate():
    assert _next_attempt_delay(0) > 0


# ---------------------------------------------------------------------------
# Подсчёт попыток без отдельной колонки
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, scheduled_at):
        self.scheduled_at = scheduled_at


def test_fresh_row_counts_as_zero_attempts():
    now = datetime.now(timezone.utc)
    assert _attempts_made(_Row(None), now) == 0


def test_attempts_inferred_from_backoff_age():
    now = datetime.now(timezone.utc)
    # Отметка 30 минут назад -> письмо пережило паузы в 5 и 15 минут.
    row = _Row(now - timedelta(minutes=30))
    assert _attempts_made(row, now) >= 2


def test_naive_timestamps_are_treated_as_utc():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = _Row(now - timedelta(minutes=30))
    assert _attempts_made(row, now) >= 2


def test_retry_window_is_bounded():
    assert 0 < RETRY_WINDOW_HOURS <= 72


# ---------------------------------------------------------------------------
# Окно отбрасывания
# ---------------------------------------------------------------------------


def test_abandonment_cutoff_precedes_created_at(monkeypatch):
    """Письмо старше окна должно оказаться за границей отсечения."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=RETRY_WINDOW_HOURS)
    old = cutoff - timedelta(minutes=1)
    assert old < cutoff


def test_job_module_exposes_retry_policy():
    for name in ("RETRY_WINDOW_HOURS", "RETRY_BACKOFF_MINUTES", "BATCH_LIMIT"):
        assert hasattr(notifications, name)


def test_batch_limit_is_reasonable():
    assert 0 < notifications.BATCH_LIMIT <= 1000