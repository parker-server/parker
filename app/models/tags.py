from sqlalchemy import Column, Integer, String, ForeignKey, Table, Index, func
from sqlalchemy.orm import relationship
from app.database import Base

# Many-to-many junction tables
comic_characters = Table(
    'comic_characters',
    Base.metadata,
    Column('comic_id', Integer, ForeignKey('comics.id', ondelete='CASCADE'), primary_key=True),
    Column('character_id', Integer, ForeignKey('characters.id', ondelete='CASCADE'), primary_key=True)
)

comic_teams = Table(
    'comic_teams',
    Base.metadata,
    Column('comic_id', Integer, ForeignKey('comics.id', ondelete='CASCADE'), primary_key=True),
    Column('team_id', Integer, ForeignKey('teams.id', ondelete='CASCADE'), primary_key=True)
)

comic_locations = Table(
    'comic_locations',
    Base.metadata,
    Column('comic_id', Integer, ForeignKey('comics.id', ondelete='CASCADE'), primary_key=True),
    Column('location_id', Integer, ForeignKey('locations.id', ondelete='CASCADE'), primary_key=True)
)

comic_genres = Table(
    'comic_genres',
    Base.metadata,
    Column('comic_id', Integer, ForeignKey('comics.id', ondelete="CASCADE"), primary_key=True),
    Column('genre_id', Integer, ForeignKey('genres.id', ondelete="CASCADE"), primary_key=True)
)

class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    __table_args__ = (Index("ux_characters_name_lower", func.lower(name), unique=True),)

    # Relationship back to comics
    comics = relationship("Comic", secondary=comic_characters, back_populates="characters")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    __table_args__ = (Index("ux_teams_name_lower", func.lower(name), unique=True),)

    # Relationship back to comics
    comics = relationship("Comic", secondary=comic_teams, back_populates="teams")


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    __table_args__ = (Index("ux_locations_name_lower", func.lower(name), unique=True),)

    # Relationship back to comics
    comics = relationship("Comic", secondary=comic_locations, back_populates="locations")


class Genre(Base):
    __tablename__ = "genres"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    __table_args__ = (Index("ux_genres_name_lower", func.lower(name), unique=True),)

    # Backref
    comics = relationship("Comic", secondary=comic_genres, back_populates="genres")
