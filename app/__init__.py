from pathlib import Path

from flask import Flask, send_from_directory
from flask_cors import CORS

from app.extensions import db, jwt, migrate
from app.models import *


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError(
            "DATABASE_URL is not set or is empty. Edit barber-backend/.env (see .env.example)."
        )

    upload_root = Path(app.config["UPLOAD_FOLDER"])
    upload_root.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    CORS(app)

    from .routes import main
    from .auth_routes import auth
    from .business_routes import business_routes
    from .plan_routes import plan_routes
    from .shop_api import shop_api
    from .public_booking import public_booking
    from .user_routes import user_routes
    from .notification_webhooks import notification_webhooks
    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(business_routes)
    app.register_blueprint(plan_routes)
    app.register_blueprint(shop_api)
    app.register_blueprint(public_booking)
    app.register_blueprint(user_routes)
    app.register_blueprint(notification_webhooks)

    @app.get("/uploads/<path:filename>")
    def serve_upload(filename: str):
        """Public file serving for business logos (and future media)."""
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    return app