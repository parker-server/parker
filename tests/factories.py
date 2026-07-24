"""Shared test factories for creating Library/LibraryRoot/Comic rows.

Comic.library_root_id/relative_path are NOT NULL, so every comic needs a real
LibraryRoot to attach to -- these helpers exist so tests don't each have to
repeat that plumbing.
"""
from app.models.comic import Comic
from app.models.library import Library
from app.models.library_root import LibraryRoot


def create_library_with_root(db, name: str, path: str, **library_kwargs) -> Library:
    """Create a Library plus its (only, active) LibraryRoot at `path`."""
    library = Library(name=name, **library_kwargs)
    db.add(library)
    db.flush()

    root = LibraryRoot(library_id=library.id, path=path, is_active=True)
    db.add(root)
    db.flush()

    return library


def create_comic(db, volume, root: LibraryRoot, relative_path: str, **comic_kwargs) -> Comic:
    """Create a Comic tied to `root` at `relative_path`."""
    comic = Comic(
        volume_id=volume.id,
        library_root_id=root.id,
        relative_path=relative_path,
        **comic_kwargs,
    )
    db.add(comic)
    db.flush()
    return comic
