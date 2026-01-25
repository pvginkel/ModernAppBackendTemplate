"""Flask-SQLAlchemy extension initialization."""

from flask_sqlalchemy import SQLAlchemy

# Flask-SQLAlchemy extension instance
# Initialized in app factory with db.init_app(app)
db = SQLAlchemy()
