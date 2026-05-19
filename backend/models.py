"""
Viralix Auth Models — SQLAlchemy ORM
Users and refresh token management for secure session tracking
"""
from sqlalchemy import create_engine, Column, String, DateTime, Boolean, Index, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timedelta
import uuid

DATABASE_URL = "sqlite:///./viralix.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,  # Set to True for SQL debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """
    User account model.
    Stores email, password hash, and optional Google OAuth ID.
    """
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=True)  # None if OAuth only
    display_name = Column(String, nullable=True)
    google_id = Column(String, nullable=True, unique=True, index=True)  # For OAuth
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "uid": self.id,
            "email": self.email,
            "displayName": self.display_name or self.email.split("@")[0],
        }


class RefreshToken(Base):
    """
    Refresh token storage and revocation tracking.
    Enables logout and token rotation management.
    """
    __tablename__ = "refresh_tokens"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, index=True)  # SHA256 of token (never store raw)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)  # Non-null = token invalidated (logout)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="refresh_tokens")

    def is_valid(self) -> bool:
        """Check if token is not revoked and not expired."""
        return self.revoked_at is None and datetime.utcnow() < self.expires_at


def init_db():
    """Create all tables. Safe to call multiple times."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for FastAPI route handlers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
