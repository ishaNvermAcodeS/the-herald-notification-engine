"""
SQLAlchemy declarative base for The Herald models.
All models must inherit from Base to be picked up by Alembic.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
