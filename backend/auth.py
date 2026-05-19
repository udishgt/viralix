"""
Viralix Auth Service — Password hashing, JWT tokens, and verification
"""
import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple
from passlib.context import CryptContext
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.models import User, RefreshToken

# ── JWT Configuration ─────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

# ── Password Hashing ──────────────────────────────────────────────────
# Use pbkdf2_sha256 for portability in dev; can switch to bcrypt in production.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def hash_token(token: str) -> str:
    """Create SHA256 hash of token for secure DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── JWT Token Models ─────────────────────────────────────────────────
class TokenPayload(BaseModel):
    sub: str  # user_id
    email: str
    type: str  # "access" or "refresh"
    exp: int


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


# ── JWT Generation & Verification ────────────────────────────────────
def create_access_token(user_id: str, email: str, expires_delta: Optional[timedelta] = None) -> str:
    """Generate JWT access token (short-lived)."""
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "exp": expire,
    }
    encoded_jwt = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(user_id: str, email: str, db: Session) -> str:
    """
    Generate JWT refresh token (long-lived) and store its hash in DB.
    Returns the raw token (client stores it).
    """
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "email": email,
        "type": "refresh",
        "exp": expires_at,
    }
    encoded_jwt = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    # Store token hash in DB (never store raw token)
    token_record = RefreshToken(
        user_id=user_id,
        token_hash=hash_token(encoded_jwt),
        expires_at=expires_at,
    )
    db.add(token_record)
    db.commit()
    
    return encoded_jwt


def verify_token(token: str, token_type: str = "access") -> Optional[TokenPayload]:
    """
    Verify JWT token signature and expiry.
    Returns payload if valid, None if invalid.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != token_type:
            return None
        return TokenPayload(**payload)
    except JWTError:
        return None


def verify_refresh_token_in_db(token: str, db: Session) -> Optional[User]:
    """
    Verify refresh token: check JWT validity and DB revocation status.
    Returns User if valid, None if invalid/revoked/expired.
    """
    payload = verify_token(token, token_type="refresh")
    if not payload:
        return None
    
    # Check token not revoked in DB
    token_hash = hash_token(token)
    token_record = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash
    ).first()
    
    if not token_record or not token_record.is_valid():
        return None
    
    # Get user
    user = db.query(User).filter(User.id == payload.sub).first()
    return user


def revoke_refresh_token(token: str, db: Session) -> bool:
    """
    Mark refresh token as revoked (logout).
    Returns True if successful, False if token not found.
    """
    token_hash = hash_token(token)
    token_record = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash
    ).first()
    
    if not token_record:
        return False
    
    token_record.revoked_at = datetime.utcnow()
    db.commit()
    return True


# ── User Authentication ──────────────────────────────────────────────
def register_user(email: str, password: str, display_name: Optional[str], db: Session) -> Tuple[Optional[User], str]:
    """
    Create a new user with email and password.
    Returns (User, error_message). Both may be non-None if there's an error.
    """
    # Check email already registered
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return None, "Email already registered"
    
    # Create user
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name or email.split("@")[0],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, ""


def authenticate_user(email: str, password: str, db: Session) -> Tuple[Optional[User], str]:
    """
    Authenticate user by email + password.
    Returns (User, error_message). If error, User is None.
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None, "Invalid email or password"
    
    if not user.password_hash:
        return None, "This account uses OAuth. Please sign in with Google."
    
    if not verify_password(password, user.password_hash):
        return None, "Invalid email or password"
    
    return user, ""


def get_user_by_id(user_id: str, db: Session) -> Optional[User]:
    """Retrieve user by ID."""
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_email(email: str, db: Session) -> Optional[User]:
    """Retrieve user by email."""
    return db.query(User).filter(User.email == email).first()
