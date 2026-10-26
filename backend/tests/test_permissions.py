"""Unit tests for the authorisation policy."""

import pytest

from careflow.errors import Forbidden
from careflow.extensions import db
from careflow.models import RolePermission, User
from careflow.security.policy import Actor, Perm, require


def _actor(roles_by_hospital):
    return Actor(user=User(email="x@example.test", full_name="X", password_hash="x"), session_id=None,
                 roles_by_hospital=roles_by_hospital)


def test_receptionist_defaults(ctx, factory):
    hid = factory.hospital()
    actor = _actor({hid: {"receptionist"}})
    assert actor.can(Perm.PATIENTS_REGISTER, hid)
    assert actor.can(Perm.QUEUE_MANAGE, hid)
    assert not actor.can(Perm.STAFF_MANAGE, hid)
    assert not actor.can(Perm.QUEUE_PRIORITY_SET, hid)
    assert not actor.can(Perm.QUEUE_CONSULT, hid)


def test_doctor_defaults(ctx, factory):
    hid = factory.hospital()
    actor = _actor({hid: {"doctor"}})
    assert actor.can(Perm.QUEUE_CONSULT, hid)
    assert not actor.can(Perm.PATIENTS_SEARCH, hid)
    assert not actor.can(Perm.AUDIT_VIEW, hid)


def test_roles_do_not_cross_hospitals(ctx, factory):
    a, b = factory.hospital("HOSA"), factory.hospital("HOSB")
    actor = _actor({a: {"hospital_admin"}})
    assert actor.can(Perm.STAFF_MANAGE, a)
    assert not actor.can(Perm.STAFF_MANAGE, b)
    with pytest.raises(Forbidden):
        require(actor, Perm.PATIENTS_VIEW, b)


def test_hospital_overrides_grant_and_revoke(ctx, factory):
    hid = factory.hospital()
    db.session.add_all([
        RolePermission(hospital_id=hid, role="receptionist", permission=Perm.QUEUE_PRIORITY_SET, allowed=True),
        RolePermission(hospital_id=hid, role="receptionist", permission=Perm.AUDIT_EXPORT, allowed=False),
    ])
    db.session.commit()
    actor = _actor({hid: {"receptionist"}})
    assert actor.can(Perm.QUEUE_PRIORITY_SET, hid)
    assert not actor.can(Perm.AUDIT_EXPORT, hid)


def test_no_roles_means_no_permissions(ctx, factory):
    hid = factory.hospital()
    assert _actor({}).permissions_in(hid) == frozenset()
