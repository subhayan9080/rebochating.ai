"""Flask app factory."""
from __future__ import annotations

import os

from flask import Flask


def create_app():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app = Flask(__name__, template_folder=os.path.join(root, "templates"),
                static_folder=os.path.join(root, "static"))
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
    app.config["RELAY_ROOT"] = root
    app.config["DB_PATH"] = os.path.join(root, "instance", "relay.db")
    app.config["UPLOAD_DIR"] = os.path.join(root, "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 9 * 1024 * 1024
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    from .db import init_db
    init_db(app.config["DB_PATH"])

    from .auth import bp as auth_bp
    from .routes import bp as dash_bp
    from .api import bp as api_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(dash_bp)
    app.register_blueprint(api_bp)

    @app.template_filter("ts")
    def _ts(value):
        import datetime
        try:
            return datetime.datetime.fromtimestamp(int(value or 0)).strftime("%b %d, %Y %H:%M")
        except (ValueError, TypeError, OSError):
            return ""

    @app.template_filter("tsd")
    def _tsd(value):
        import datetime
        try:
            return datetime.datetime.fromtimestamp(int(value or 0)).strftime("%b %d, %Y")
        except (ValueError, TypeError, OSError):
            return ""

    @app.errorhandler(404)
    def _404(e):
        from flask import render_template
        return render_template("404.html"), 404

    return app
