"""Flask extension instances, initialised in the application factory."""

from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_smorest import Api as _Api
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData

from careflow.errors import http_exception_response

# Deterministic constraint names so Alembic migrations are stable.
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Api(_Api):
    """flask-smorest Api using CareFlow's uniform error format."""

    def handle_http_exception(self, error):
        return http_exception_response(error)


db = SQLAlchemy(metadata=MetaData(naming_convention=naming_convention))
migrate = Migrate()
jwt = JWTManager()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
cors = CORS()
api = Api()
