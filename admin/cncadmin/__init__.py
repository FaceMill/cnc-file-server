import datetime
import logging

from flask import Flask

from . import config, serial_dnc
from .network import office_change


def create_app():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.secret_key(),
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # uploads (NC programs)
    )
    app.jinja_env.filters["timestamp"] = (
        lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M"))
    from .web import bp
    app.register_blueprint(bp)
    office_change.recover()
    if config.exists():
        serial_dnc.manager.sync(config.load())
    return app
