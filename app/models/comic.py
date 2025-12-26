from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, Float, JSON, Index, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base

# Import the junction tables
from app.models.tags import comic_characters, comic_teams, comic_locations, comic_genres


class Volume(Base):
    __tablename__ = "volumes"

    id = Column(Integer, primary_key=True, index=True)
    series_id = Column(Integer, ForeignKey("series.id"))
    volume_number = Column(Integer, default=1)

    series = relationship("Series", back_populates="volumes")
    comics = relationship("Comic", back_populates="volume", cascade="all, delete-orphan")


class Comic(Base):
    __tablename__ = "comics"

    __table_args__ = (
        Index('idx_comic_volume_age_rating', 'volume_id', 'age_rating'),
    )

    id = Column(Integer, primary_key=True, index=True)
    volume_id = Column(Integer, ForeignKey("volumes.id"))

    filename = Column(String, nullable=False)
    file_path = Column(String, unique=True, nullable=False)
    file_modified_at = Column(Float)
    file_size = Column(Integer)
    thumbnail_path = Column(String, nullable=True)  # Path to cached thumbnail
    page_count = Column(Integer, default=0)

    # Basic metadata
    number = Column(String)
    title = Column(String)
    summary = Column(Text)
    year = Column(Integer)
    month = Column(Integer)
    day = Column(Integer)
    web = Column(String)
    notes = Column(Text)
    age_rating = Column(String, nullable=True, index=True) # e.g. "Everyone"
    language_iso = Column(String, nullable=True)  # e.g. "en", "jp"
    community_rating = Column(Float, nullable=True, default=None)

    # Total issue count for the volume (derived during scan from actual page counting in archive)
    # Used to determine if a series is "Ended" and calculate missing issues.
    count = Column(Integer, nullable=True)

    # Credits now handled via relationships (removed individual fields)
    credits = relationship("ComicCredit", back_populates="comic", cascade="all, delete-orphan")

    # Publishing info
    publisher = Column(String, index=True)
    imprint = Column(String)
    format = Column(String)
    series_group = Column(String, index=True)

    # Scan info
    scan_information = Column(String)

    # Many-to-many relationships for tags
    characters = relationship("Character", secondary=comic_characters, back_populates="comics")
    teams = relationship("Team", secondary=comic_teams, back_populates="comics")
    locations = relationship("Location", secondary=comic_locations, back_populates="comics")
    genres = relationship("Genre", secondary=comic_genres, back_populates="comics")

    # Reading list support
    alternate_series = Column(String)
    alternate_number = Column(String)
    story_arc = Column(String)

    # Dominant Colors (used for Colorscape) (Stored as HEX strings e.g., "#FF0000")
    color_primary = Column(String, nullable=True)
    color_secondary = Column(String, nullable=True)
    color_palette = Column(JSON, nullable=True)  # Full color palette for advanced features

    # Store full metadata as JSON for anything we missed
    metadata_json = Column(Text)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_dirty = Column(Boolean, default=False, index=True)  # Add index for faster polling


    # Relationships
    volume = relationship("Volume", back_populates="comics")
    reading_list_items = relationship("ReadingListItem", back_populates="comic", cascade="all, delete-orphan")
    collection_items = relationship("CollectionItem", back_populates="comic", cascade="all, delete-orphan")
    reading_progress = relationship("ReadingProgress", back_populates="comic", cascade="all, delete-orphan")
    pull_list_items = relationship("PullListItem", back_populates="comic", cascade="all, delete-orphan")


    # Helper methods to get credits by role
    def get_credits_by_role(self, role: str) -> list:
        """Get all people for a specific role"""
        return [credit.person.name for credit in self.credits if credit.role == role]

    @property
    def writers(self):
        return self.get_credits_by_role('writer')

    @property
    def pencillers(self):
        return self.get_credits_by_role('penciller')

    @property
    def inkers(self):
        return self.get_credits_by_role('inker')

    @property
    def colorists(self):
        return self.get_credits_by_role('colorist')

    @property
    def letterers(self):
        return self.get_credits_by_role('letterer')

    @property
    def cover_artists(self):
        return self.get_credits_by_role('cover_artist')

    @property
    def editors(self):
        return self.get_credits_by_role('editor')