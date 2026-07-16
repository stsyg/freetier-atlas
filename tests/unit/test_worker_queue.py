"""Unit tests for the worker/scheduler pure logic (no live database required)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from worker import queue
from worker.health import check_service
from worker.heartbeat import is_fresh
from worker.main import process_job


class TestJobStateTransitions:
    def test_running_to_done_on_success(self) -> None:
        assert queue.next_status(queue.STATUS_RUNNING, success=True) == queue.STATUS_DONE

    def test_running_to_failed_on_error(self) -> None:
        assert queue.next_status(queue.STATUS_RUNNING, success=False) == queue.STATUS_FAILED

    @pytest.mark.parametrize(
        "state", [queue.STATUS_PENDING, queue.STATUS_DONE, queue.STATUS_FAILED]
    )
    def test_cannot_complete_non_running(self, state: str) -> None:
        with pytest.raises(ValueError):
            queue.next_status(state, success=True)

    def test_valid_statuses_are_the_four_lifecycle_states(self) -> None:
        assert queue.VALID_STATUSES == {"pending", "running", "done", "failed"}


class TestClaimSqlConstruction:
    def test_claim_uses_for_update_skip_locked(self) -> None:
        sql = str(queue.CLAIM_SQL)
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "status = 'running'" in sql
        assert "attempts = attempts + 1" in sql

    def test_enqueue_casts_payload_to_jsonb(self) -> None:
        assert "CAST(:payload AS JSONB)" in str(queue.ENQUEUE_SQL)

    def test_mark_done_and_failed_set_finished_at(self) -> None:
        assert "status = 'done'" in str(queue.MARK_DONE_SQL)
        assert "finished_at = now()" in str(queue.MARK_DONE_SQL)
        assert "status = 'failed'" in str(queue.MARK_FAILED_SQL)
        assert "last_error = :error" in str(queue.MARK_FAILED_SQL)


class TestHeartbeatFreshness:
    def test_fresh_when_within_threshold(self) -> None:
        now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
        last = now - timedelta(seconds=10)
        assert is_fresh(last, now, stale_seconds=30) is True

    def test_stale_when_beyond_threshold(self) -> None:
        now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
        last = now - timedelta(seconds=45)
        assert is_fresh(last, now, stale_seconds=30) is False

    def test_boundary_is_inclusive(self) -> None:
        now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
        last = now - timedelta(seconds=30)
        assert is_fresh(last, now, stale_seconds=30) is True

    def test_future_beat_is_treated_as_fresh(self) -> None:
        now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
        last = now + timedelta(seconds=5)
        assert is_fresh(last, now, stale_seconds=30) is True


class TestProcessJob:
    def test_heartbeat_job_succeeds(self) -> None:
        # No exception means success (worker marks it done).
        process_job("heartbeat", {"source": "scheduler"})

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError):
            process_job("scan-cloudflare", {})


class TestHealthDoesNotLeakSecrets:
    def test_database_error_message_has_no_credentials(self, monkeypatch) -> None:
        import worker.health as health_module

        def boom() -> None:
            raise RuntimeError("password=atlas host=postgres")

        monkeypatch.setattr(health_module, "get_engine", boom)
        healthy, message = check_service("worker")
        assert healthy is False
        lowered = message.lower()
        assert "atlas" not in lowered
        assert "postgres" not in lowered
        assert "password" not in lowered
        assert "RuntimeError" in message
