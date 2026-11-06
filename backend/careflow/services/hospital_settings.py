"""Per-hospital operational policy with documented defaults.

Values are stored in `hospitals.settings` (JSONB); anything missing falls back to DEFAULTS.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from careflow.models import Hospital

DEFAULTS: dict[str, int] = {
    # Booking
    "booking_max_days_ahead": 60,        # how far ahead patients can request
    "booking_min_lead_minutes": 60,      # latest a patient can request before the slot starts
    "max_open_requests_per_patient": 5,  # pending requests per patient per hospital
    "patient_cancel_min_minutes": 0,     # patients can cancel until this many minutes before start
    # Attendance
    "no_show_grace_minutes": 30,         # staff can mark no-show this long after start
    # Check-in and queue
    "allow_patient_self_checkin": 1,     # 1 = patients may check in from their phone
    "checkin_open_minutes_before": 60,   # check-in opens this long before the appointment
    "checkin_close_minutes_after": 60,   # and closes this long after its start
    "checkin_grace_minutes": 15,         # arriving within this of the start keeps the booked place
    "max_missed_calls": 2,               # after this many missed calls, staff cannot return the patient
    "require_arrival_confirmation": 0,   # 1 = self-checked-in patients must be confirmed by staff before being called
    # Wait-time estimation
    "estimate_min_samples": 10,          # completed consultations needed before history is trusted
    "estimate_history_days": 60,         # how far back to look for consultation durations
}


def get_setting(hospital: Hospital, key: str) -> int:
    if key not in DEFAULTS:
        raise KeyError(key)
    value = (hospital.settings or {}).get(key, DEFAULTS[key])
    return int(value)


def hospital_tz(hospital: Hospital) -> ZoneInfo:
    return ZoneInfo(hospital.timezone)
