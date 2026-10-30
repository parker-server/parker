from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class Person(Base):
    """A person who contributes to comics (writer, artist, etc.)"""
    __tablename__ = "people"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)

    # Relationship to credits
    credits = relationship("ComicCredit", back_populates="person", cascade="all, delete-orphan")


class ComicCredit(Base):
    """Junction table linking comics to people with a specific role"""
    __tablename__ = "comic_credits"

    id = Column(Integer, primary_key=True, index=True)
    comic_id = Column(Integer, ForeignKey('comics.id', ondelete='CASCADE'), nullable=False)
    person_id = Column(Integer, ForeignKey('people.id', ondelete='CASCADE'), nullable=False, index=True)
    role = Column(String, nullable=False, index=True)  # 'writer', 'penciller', 'inker', etc.

    # Prevent duplicate person+role on same comic
    __table_args__ = (
        UniqueConstraint('comic_id', 'person_id', 'role', name='unique_comic_person_role'),
    )

    # Relationships
    comic = relationship("Comic", back_populates="credits")
    person = relationship("Person", back_populates="credits")