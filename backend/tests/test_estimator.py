"""Unit tests for the waiting-time estimator (no database)."""

from datetime import UTC, datetime, timedelta

from careflow.services.estimator import EstimateInputs, duration_stats, estimate

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def _inputs(**overrides):
    base = dict(now=NOW, entry_status="waiting", patients_ahead=0, doctor_status="available",
                expected_return_at=None, next_session_start=None, queue_paused=False,
                with_doctor_status=None, with_doctor_started_at=None,
                stats=duration_stats([], fallback_minutes=10, min_samples=10))
    base.update(overrides)
    return EstimateInputs(**base)


def test_fallback_is_provisional():
    s = duration_stats([12, 14], fallback_minutes=10, min_samples=10)
    assert s.provisional and s.typical == 10 and s.low == 7.5 and s.high == 15 and s.sample_size == 2


def test_history_uses_median_and_quartiles():
    s = duration_stats([8, 9, 10, 10, 11, 12, 12, 13, 14, 15], fallback_minutes=30, min_samples=10)
    assert not s.provisional
    assert s.typical == 11.5
    assert s.low < s.typical < s.high


def test_outliers_are_trimmed():
    data = [10] * 9 + [11, 300]
    s = duration_stats(data, fallback_minutes=15, min_samples=10)
    assert s.sample_size == 10 and s.high < 20


def test_next_patient_with_free_doctor():
    est = estimate(_inputs())
    assert est.state == "next" and (est.min_minutes, est.max_minutes) == (0, 5)
    assert est.provisional and "provisional" in est.explanation


def test_patients_ahead_range():
    stats = duration_stats([10] * 12, fallback_minutes=15, min_samples=10)
    est = estimate(_inputs(patients_ahead=3, stats=stats))
    assert est.state == "estimated" and not est.provisional
    # 3 ahead x 10-11 minutes = 30-33 -> rounded to 30-35
    assert (est.min_minutes, est.max_minutes) == (30, 35)
    assert est.basis["patients_ahead"] == 3


def test_range_always_spans_at_least_five_minutes_and_is_rounded():
    est = estimate(_inputs(patients_ahead=2))  # fallback 7.5-15 per patient
    assert est.min_minutes % 5 == 0 and est.max_minutes % 5 == 0
    assert est.max_minutes - est.min_minutes >= 5
    assert (est.min_minutes, est.max_minutes) == (15, 30)


def test_current_consultation_remaining_time():
    started = NOW - timedelta(minutes=5)
    est = estimate(_inputs(with_doctor_status="in_consultation", with_doctor_started_at=started))
    # fallback low 7.5 - 5 elapsed = 2.5, high 15 - 5 = 10
    assert est.state == "estimated" and (est.min_minutes, est.max_minutes) == (0, 10)


def test_overrunning_consultation_still_counts_a_little():
    started = NOW - timedelta(minutes=60)
    est = estimate(_inputs(with_doctor_status="in_consultation", with_doctor_started_at=started))
    assert est.max_minutes >= 5


def test_break_with_expected_return_adds_offset():
    est = estimate(_inputs(doctor_status="on_break", expected_return_at=NOW + timedelta(minutes=20), patients_ahead=1))
    assert est.min_minutes >= 25 and est.basis["minutes_until_doctor_available"] == 20


def test_unavailable_without_return_time_gives_no_number():
    for status in ("on_break", "temporarily_unavailable"):
        est = estimate(_inputs(doctor_status=status))
        assert est.state == "unavailable" and est.min_minutes is None
    assert estimate(_inputs(queue_paused=True)).state == "unavailable"


def test_checked_out_doctor():
    est = estimate(_inputs(doctor_status="checked_out"))
    assert est.state == "unavailable" and "finished" in est.explanation


def test_doctor_not_arrived():
    assert estimate(_inputs(doctor_status="off_duty")).state == "unavailable"
    est = estimate(_inputs(doctor_status="off_duty", next_session_start=NOW + timedelta(minutes=30)))
    assert est.state == "estimated" and est.min_minutes >= 30


def test_called_and_being_seen_states():
    assert estimate(_inputs(entry_status="called")).state == "called"
    assert estimate(_inputs(entry_status="in_consultation")).state == "being_seen"
    assert estimate(_inputs(entry_status="completed")).state == "not_waiting"
