import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

DATABASE_URL = settings.database_url

# Railway pe /tmp writable hota hai — SQLite path fix
if DATABASE_URL.startswith("sqlite:///./"):
    db_file = DATABASE_URL.replace("sqlite:///./", "")
    # Railway environment mein /tmp use karo
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        DATABASE_URL = f"sqlite:////tmp/{db_file}"


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
