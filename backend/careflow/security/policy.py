"""Server-side authorisation policy.

All permission decisions go through this module. Route handlers and services call
`require(actor, permission, hospital_id)`; clients are never trusted to decide.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from careflow.errors import Forbidden
from careflow.extensions import db
from careflow.models import RolePermission, User


class Perm:
    PATIENTS_REGISTER = "patients.register"
    PATIENTS_SEARCH = "patients.search"
    PATIENTS_VIEW = "patients.view"
    DEPARTMENTS_MANAGE = "departments.manage"
    STAFF_MANAGE = "staff.manage"
    AUDIT_VIEW = "audit.view"
    AUDIT_EXPORT = "audit.export"
    APPOINTMENTS_REVIEW = "appointments.review"
    QUEUE_MANAGE = "queue.manage"
    QUEUE_PRIORITY_SET = "queue.priority.set"
    QUEUE_CORRECT = "queue.correct"
    QUEUE_CONSULT = "queue.consult"
    AVAILABILITY_SELF = "availability.self"
    AVAILABILITY_VERIFY = "availability.verify"


ALL_PERMISSIONS = frozenset(v for k, v in vars(Perm).items() if not k.startswith("_"))

DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "receptionist": frozenset({
        Perm.PATIENTS_REGISTER,
        Perm.PATIENTS_SEARCH,
        Perm.PATIENTS_VIEW,
        Perm.AUDIT_VIEW,
        Perm.AUDIT_EXPORT,
        Perm.APPOINTMENTS_REVIEW,
        Perm.QUEUE_MANAGE,
        Perm.AVAILABILITY_VERIFY,
    }),
    "doctor": frozenset({
        Perm.QUEUE_CONSULT,
        Perm.AVAILABILITY_SELF,
    }),
    "hospital_admin": ALL_PERMISSIONS - {Perm.QUEUE_CONSULT, Perm.AVAILABILITY_SELF},
}


@dataclass
class Actor:
    """The authenticated caller, loaded fresh from the database on every request."""

    user: User
    session_id: uuid.UUID
    # hospital_id -> set of active roles
    roles_by_hospital: dict[uuid.UUID, set[str]] = field(default_factory=dict)
    patient_id: uuid.UUID | None = None
    _perm_cache: dict[uuid.UUID, frozenset[str]] = field(default_factory=dict, repr=False)

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def is_staff(self) -> bool:
        return bool(self.roles_by_hospital)

    def roles_in(self, hospital_id: uuid.UUID) -> set[str]:
        return self.roles_by_hospital.get(hospital_id, set())

    def permissions_in(self, hospital_id: uuid.UUID) -> frozenset[str]:
        if hospital_id not in self._perm_cache:
            self._perm_cache[hospital_id] = _effective_permissions(hospital_id, self.roles_in(hospital_id))
        return self._perm_cache[hospital_id]

    def can(self, permission: str, hospital_id: uuid.UUID) -> bool:
        return permission in self.permissions_in(hospital_id)


def _effective_permissions(hospital_id: uuid.UUID, roles: set[str]) -> frozenset[str]:
    if not roles:
        return frozenset()
    overrides = db.session.execute(
        select(RolePermission.role, RolePermission.permission, RolePermission.allowed).where(
            RolePermission.hospital_id == hospital_id, RolePermission.role.in_(roles)
        )
    ).all()
    granted: set[str] = set()
    for role in roles:
        perms = set(DEFAULT_ROLE_PERMISSIONS.get(role, frozenset()))
        for o_role, perm, allowed in overrides:
            if o_role == role:
                (perms.add if allowed else perms.discard)(perm)
        granted |= perms
    return frozenset(granted)


def require(actor: Actor, permission: str, hospital_id: uuid.UUID) -> None:
    if not actor.can(permission, hospital_id):
        raise Forbidden()


def require_patient(actor: Actor) -> uuid.UUID:
    if actor.patient_id is None:
        raise Forbidden("This action is only available to patients.")
    return actor.patient_id
