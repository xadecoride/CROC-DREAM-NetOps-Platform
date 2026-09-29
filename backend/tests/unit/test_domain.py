"""Job state machine, enums and settings."""

from __future__ import annotations

from datetime import datetime

import pytest

from netops.db import TZDateTime, build_engine
from netops.enums import JobStatus, JobType, TargetStatus, UserRole
from netops.errors import InvalidJobTransitionError
from netops.models import ALLOWED_TRANSITIONS, Job, JobTarget
from netops.settings import Settings


def _job(status: JobStatus = JobStatus.PENDING) -> Job:
    return Job(type=JobType.DRY_RUN, status=status, progress=0, created_by="test")


class TestJobStateMachine:
    def test_happy_path_stamps_timestamps(self) -> None:
        job = _job()
        job.transition_to(JobStatus.RUNNING)
        assert job.started_at is not None
        assert job.finished_at is None

        job.transition_to(JobStatus.SUCCESS)
        assert job.status is JobStatus.SUCCESS
        assert job.progress == 100
        assert job.finished_at is not None
        assert job.finished_at >= job.started_at
        assert job.error is None

    def test_failure_records_the_error(self) -> None:
        job = _job(JobStatus.RUNNING)
        job.transition_to(JobStatus.FAILED, error="boom")
        assert job.error == "boom"
        assert job.finished_at is not None

    def test_pending_job_can_fail_without_running(self) -> None:
        job = _job()
        job.transition_to(JobStatus.FAILED, error="queue down")
        assert job.started_at is None
        assert job.status is JobStatus.FAILED

    @pytest.mark.parametrize(
        ("current", "requested"),
        [
            (JobStatus.PENDING, JobStatus.SUCCESS),
            (JobStatus.PENDING, JobStatus.PENDING),
            (JobStatus.RUNNING, JobStatus.PENDING),
            (JobStatus.RUNNING, JobStatus.RUNNING),
            (JobStatus.SUCCESS, JobStatus.FAILED),
            (JobStatus.FAILED, JobStatus.RUNNING),
        ],
    )
    def test_invalid_transitions(self, current: JobStatus, requested: JobStatus) -> None:
        job = _job(current)
        with pytest.raises(InvalidJobTransitionError):
            job.transition_to(requested)
        assert job.status is current

    def test_terminal_states_have_no_exits(self) -> None:
        for status in JobStatus:
            assert (not ALLOWED_TRANSITIONS[status]) is status.is_terminal


def test_target_has_changes() -> None:
    assert not JobTarget(hostname="a").has_changes
    assert not JobTarget(hostname="a", remediation_config="\n").has_changes
    assert JobTarget(hostname="a", remediation_config="hostname b\n").has_changes


def test_target_failure_statuses() -> None:
    assert {s for s in TargetStatus if s.is_failure} == {
        TargetStatus.FAILED,
        TargetStatus.ROLLED_BACK,
    }


def test_job_types_that_change_devices() -> None:
    assert {t for t in JobType if t.changes_devices} == {JobType.DEPLOY, JobType.DRIFT_REMEDIATE}


@pytest.mark.parametrize(
    ("role", "required", "granted"),
    [
        (UserRole.ADMIN, UserRole.OPERATOR, True),
        (UserRole.OPERATOR, UserRole.OPERATOR, True),
        (UserRole.OPERATOR, UserRole.ADMIN, False),
        (UserRole.VIEWER, UserRole.OPERATOR, False),
        (UserRole.VIEWER, UserRole.VIEWER, True),
    ],
)
def test_role_hierarchy(role: UserRole, required: UserRole, granted: bool) -> None:
    assert role.grants(required) is granted


def test_settings_authenticate() -> None:
    settings = Settings(
        _env_file=None,
        api_tokens={"t0ken": {"username": "alice", "role": "admin"}},
    )
    principal = settings.authenticate("t0ken")
    assert principal is not None
    assert principal.username == "alice"
    assert principal.role is UserRole.ADMIN
    assert settings.authenticate("t0ke") is None
    assert settings.authenticate("") is None


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETOPS_DRIFT_SCAN_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("NETOPS_AUTH_PROFILES", '{"lab": {"username": "admin", "password": "pw"}}')
    settings = Settings(_env_file=None)
    assert settings.drift_scan_interval_seconds == 600
    assert settings.auth_profiles["lab"].password.get_secret_value() == "pw"
    assert "pw" not in repr(settings)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="Naive datetimes"):
        TZDateTime().process_bind_param(datetime(2026, 1, 1), dialect=None)  # type: ignore[arg-type]


def test_postgres_engine_checks_connections() -> None:
    engine = build_engine("postgresql+psycopg://user:pw@db.invalid/netops")
    assert engine.pool._pre_ping is True
    engine.dispose()


def test_post_check_retries_must_fit_the_confirm_timer() -> None:
    fine = Settings(_env_file=None, post_check_attempts=6, post_check_interval_seconds=10)
    assert fine.post_check_attempts == 6
    with pytest.raises(ValueError, match="more than half"):
        Settings(
            _env_file=None,
            commit_confirm_timeout_seconds=60,
            post_check_attempts=10,
            post_check_interval_seconds=10,
        )
