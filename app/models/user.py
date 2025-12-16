from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base

# Many to many Junction Table
user_libraries = Table(
    'user_libraries',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete="CASCADE"), primary_key=True),
    Column('library_id', Integer, ForeignKey('libraries.id', ondelete="CASCADE"), primary_key=True)
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    avatar_path = Column(String, nullable=True)
    share_progress_enabled = Column(Boolean, default=False)

    max_age_rating = Column(String, nullable=True, default=None)
    allow_unknown_age_ratings = Column(Boolean, default=False)

    # Permissions
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)

    # Relationships

    # When a user is deleted, delete their reading history too
    reading_progress = relationship("ReadingProgress", back_populates="user", cascade="all, delete-orphan")

    # Many-to-Many Relationship
    # We use a string "Library" to avoid circular imports if Library imports User
    accessible_libraries = relationship("Library", secondary=user_libraries, backref="allowed_users")

    # Pull Lists Relationship. use the string "PullList" to avoid circular imports.
    pull_lists = relationship("PullList", back_populates="user", cascade="all, delete-orphan")
