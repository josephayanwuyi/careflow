"""Patient profiles, walk-in registration, and hospital-scoped patient lookup."""

from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import func, or_, select

from careflow.errors import Conflict, NotFound, ValidationFailed
from careflow.extensions import db
from careflow.models import Hospital, HospitalPatientRecord, PatientProfile, User
from careflow.security.policy import Actor, Perm, require, require_patient
from careflow.services import audit_service
from careflow.services.hospital_service import get_hospital
from careflow.services.normalize import normalize_name, normalize_phone

SEARCH_LIMIT = 20
_UNSET = object()


# --- Patient self-service ------------------------------------------------------------


def get_my_profile(actor: Actor) -> PatientProfile:
    return db.session.get(PatientProfile, require_patient(actor))


def update_my_profile(actor: Actor, *, full_name=_UNSET, phone=_UNSET, date_of_birth=_UNSET, sex=_UNSET) -> PatientProfile:
    profile = get_my_profile(actor)
    changed: list[str] = []
    if full_name is not _UNSET:
        profile.full_name = full_name.strip()
        actor.user.full_name = profile.full_name
        changed.append("full_name")
    if phone is not _UNSET:
        new_phone = normalize_phone(phone) if phone else None
        if new_phone and db.session.scalar(select(User.id).where(User.phone == new_phone, User.id != actor.user_id)):
            raise Conflict("This phone number is already used by another account.", code="phone_taken")
        profile.phone = new_phone
        actor.user.phone = new_phone
        changed.append("phone")
    if date_of_birth is not _UNSET:
        profile.date_of_birth = date_of_birth
        changed.append("date_of_birth")
    if sex is not _UNSET:
        profile.sex = sex
        changed.append("sex")
    if changed:
        audit_service.record("patient.profile_updated", actor=actor, target_type="patient", target_id=profile.id,
                             details={"fields": changed})
        db.session.commit()
    return profile


# --- Hospital-scoped operations (staff) -----------------------------------------------


def _lock_hospital(hospital_id: uuid.UUID) -> Hospital:
    """Lock the hospital row and re-read it from the database (not the session cache)."""
    return db.session.scalar(select(Hospital).where(Hospital.id == hospital_id).with_for_update()
                             .execution_options(populate_existing=True))


def ensure_hospital_record(hospital_id: uuid.UUID, patient_id: uuid.UUID, created_by: uuid.UUID | None) -> HospitalPatientRecord:
    """Return the patient's record at this hospital, creating it if needed. Caller commits.

    The hospital row lock serialises creation, so concurrent calls neither duplicate the record
    nor hand out the same patient number.
    """
    def existing():
        return db.session.scalar(select(HospitalPatientRecord).where(
            HospitalPatientRecord.hospital_id == hospital_id, HospitalPatientRecord.patient_id == patient_id))

    record = existing()
    if record is not None:
        return record
    hospital = _lock_hospital(hospital_id)
    record = existing()  # re-check now that we hold the lock
    if record is None:
        number = f"{hospital.code}-{hospital.next_patient_number:06d}"
        hospital.next_patient_number += 1
        record = HospitalPatientRecord(hospital_id=hospital_id, patient_id=patient_id,
                                       hospital_patient_number=number, created_by_user_id=created_by)
        db.session.add(record)
        db.session.flush()
    return record


def find_possible_duplicates(hospital_id: uuid.UUID, *, full_name: str, phone: str | None,
                             date_of_birth: date | None) -> list[tuple[PatientProfile, HospitalPatientRecord]]:
    """Patients at this hospital with the same phone, or the same name and date of birth."""
    conditions = []
    if phone:
        conditions.append(PatientProfile.phone == phone)
    if date_of_birth:
        conditions.append(
            (func.lower(func.regexp_replace(PatientProfile.full_name, r"\s+", " ", "g")) == normalize_name(full_name))
            & (PatientProfile.date_of_birth == date_of_birth)
        )
    if not conditions:
        conditions.append(func.lower(func.regexp_replace(PatientProfile.full_name, r"\s+", " ", "g")) == normalize_name(full_name))
    rows = db.session.execute(
        select(PatientProfile, HospitalPatientRecord)
        .join(HospitalPatientRecord, HospitalPatientRecord.patient_id == PatientProfile.id)
        .where(HospitalPatientRecord.hospital_id == hospital_id, or_(*conditions))
        .limit(10)
    ).all()
    return [(p, r) for p, r in rows]


def register_walk_in(
    actor: Actor,
    hospital_id: uuid.UUID,
    *,
    full_name: str,
    phone: str | None,
    date_of_birth: date | None,
    sex: str | None,
    confirm_not_duplicate: bool,
    duplicate_override_reason: str | None,
) -> tuple[PatientProfile, HospitalPatientRecord]:
    get_hospital(hospital_id)
    require(actor, Perm.PATIENTS_REGISTER, hospital_id)
    phone = normalize_phone(phone) if phone else None

    # Serialise registrations per hospital so two receptionists cannot both pass the
    # duplicate check for the same person at the same moment.
    _lock_hospital(hospital_id)
    candidates = find_possible_duplicates(hospital_id, full_name=full_name, phone=phone, date_of_birth=date_of_birth)
    if candidates and not confirm_not_duplicate:
        raise Conflict(
            "A patient with matching details is already registered at this hospital.",
            code="possible_duplicate",
            details={"candidates": [
                {"patient_id": str(p.id), "hospital_patient_number": r.hospital_patient_number,
                 "full_name": p.full_name, "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
                 "phone_last4": p.phone[-4:] if p.phone else None}
                for p, r in candidates
            ]},
        )
    if candidates and not (duplicate_override_reason and duplicate_override_reason.strip()):
        raise ValidationFailed(details={"duplicate_override_reason": ["A reason is required when registering despite possible duplicates."]})

    patient = PatientProfile(full_name=" ".join(full_name.split()), phone=phone, date_of_birth=date_of_birth,
                             sex=sex, created_by_user_id=actor.user_id)
    db.session.add(patient)
    db.session.flush()
    record = ensure_hospital_record(hospital_id, patient.id, actor.user_id)
    audit_service.record(
        "patient.registered_walk_in", actor=actor, hospital_id=hospital_id, target_type="patient", target_id=patient.id,
        reason=duplicate_override_reason if candidates else None,
        details={"duplicate_candidates": [str(p.id) for p, _ in candidates]} if candidates else None,
    )
    db.session.commit()
    return patient, record


def search_patients(actor: Actor, hospital_id: uuid.UUID, query: str) -> list[tuple[PatientProfile, HospitalPatientRecord]]:
    """Search by hospital patient number, phone number, or name (min. 3 characters)."""
    get_hospital(hospital_id)
    require(actor, Perm.PATIENTS_SEARCH, hospital_id)
    q = query.strip()
    if len(q) < 3:
        raise ValidationFailed(details={"q": ["Enter at least 3 characters."]})

    stmt = (select(PatientProfile, HospitalPatientRecord)
            .join(HospitalPatientRecord, HospitalPatientRecord.patient_id == PatientProfile.id)
            .where(HospitalPatientRecord.hospital_id == hospital_id))
    if re.fullmatch(r"[A-Za-z0-9]{2,10}-\d+", q):
        match_type = "hospital_patient_number"
        stmt = stmt.where(HospitalPatientRecord.hospital_patient_number == q.upper())
    elif re.fullmatch(r"[+\d\s\-()]{7,}", q):
        match_type = "phone"
        stmt = stmt.where(PatientProfile.phone == normalize_phone(q))
    else:
        match_type = "name"
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(PatientProfile.full_name.ilike(f"%{escaped}%", escape="\\"))
    rows = db.session.execute(stmt.order_by(PatientProfile.full_name).limit(SEARCH_LIMIT)).all()
    audit_service.record("patient.searched", actor=actor, hospital_id=hospital_id,
                         details={"match_type": match_type, "result_count": len(rows)})
    db.session.commit()
    return [(p, r) for p, r in rows]


def get_patient_at_hospital(actor: Actor, hospital_id: uuid.UUID, patient_id: uuid.UUID) -> tuple[PatientProfile, HospitalPatientRecord]:
    require(actor, Perm.PATIENTS_VIEW, hospital_id)
    row = db.session.execute(
        select(PatientProfile, HospitalPatientRecord)
        .join(HospitalPatientRecord, HospitalPatientRecord.patient_id == PatientProfile.id)
        .where(HospitalPatientRecord.hospital_id == hospital_id, PatientProfile.id == patient_id)
    ).first()
    if row is None:
        raise NotFound("Patient not found.")
    audit_service.record("patient.viewed", actor=actor, hospital_id=hospital_id, target_type="patient", target_id=patient_id)
    db.session.commit()
    return row[0], row[1]
