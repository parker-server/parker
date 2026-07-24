from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.core.path_utils import compute_relative_path, paths_overlap, resolve_absolute_path
from app.models.comic import Comic
from app.models.library import Library
from app.models.library_root import LibraryRoot


DEFAULT_SAMPLE_LIMIT = 10
NO_RELOCATION_MATCHES_MESSAGE = (
    "No existing comics were found at the new path. Move the library files first, "
    "preserving the same folder structure, then preview again."
)


class LibraryRelocationError(ValueError):
    pass


@dataclass(frozen=True)
class RelocationPathSample:
    relative_path: str
    path: str

    def to_dict(self) -> dict:
        return {
            "relative_path": self.relative_path,
            "path": self.path,
        }


@dataclass(frozen=True)
class LibraryRootRelocationPreview:
    library_id: int
    root_id: int
    current_path: str
    proposed_path: str
    total_existing: int
    total_scanned: int
    matched_count: int
    missing_count: int
    new_count: int
    matched_samples: list[RelocationPathSample]
    missing_samples: list[RelocationPathSample]
    new_samples: list[RelocationPathSample]

    @property
    def confirm_blocked(self) -> bool:
        return self.total_existing > 0 and self.matched_count == 0

    @property
    def confirm_blocked_reason(self) -> str | None:
        if not self.confirm_blocked:
            return None

        return NO_RELOCATION_MATCHES_MESSAGE

    def to_dict(self) -> dict:
        return {
            "library_id": self.library_id,
            "root_id": self.root_id,
            "current_path": self.current_path,
            "proposed_path": self.proposed_path,
            "total_existing": self.total_existing,
            "total_scanned": self.total_scanned,
            "matched_count": self.matched_count,
            "missing_count": self.missing_count,
            "new_count": self.new_count,
            "matched_samples": [sample.to_dict() for sample in self.matched_samples],
            "missing_samples": [sample.to_dict() for sample in self.missing_samples],
            "new_samples": [sample.to_dict() for sample in self.new_samples],
            "confirm_blocked": self.confirm_blocked,
            "confirm_blocked_reason": self.confirm_blocked_reason,
        }


@dataclass(frozen=True)
class LibraryRootRelocationConfirmation:
    preview: LibraryRootRelocationPreview
    scan_recommended: bool
    scan_reasons: list[str]

    def to_dict(self) -> dict:
        payload = self.preview.to_dict()
        payload["previous_path"] = payload.pop("current_path")
        payload["current_path"] = self.preview.proposed_path
        payload["relocated"] = True
        payload["scan_recommended"] = self.scan_recommended
        payload["scan_reasons"] = self.scan_reasons
        return payload


def preview_library_root_relocation(
    db: Session,
    *,
    library: Library,
    proposed_path: str,
    root_id: int | None = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> LibraryRootRelocationPreview:
    selected_root = _select_relocation_root(library, root_id)
    resolved_proposed_path = _resolve_proposed_path(proposed_path)
    resolved_proposed_path_str = _path_for_storage(resolved_proposed_path)
    current_comparison_path = _comparison_path(selected_root.path)

    if _paths_equal(current_comparison_path, resolved_proposed_path_str):
        raise LibraryRelocationError("Relocation path must be different from the current root path")

    if paths_overlap(current_comparison_path, resolved_proposed_path_str):
        raise LibraryRelocationError("Relocation path overlaps with the current root path")

    overlapping_root = _find_overlapping_root(
        db,
        resolved_proposed_path_str,
        exclude_root_id=selected_root.id,
    )
    if overlapping_root is not None:
        library_name = overlapping_root.library.name if overlapping_root.library else "unknown"
        raise LibraryRelocationError(
            f"Relocation path overlaps with existing root for library '{library_name}'"
        )

    sample_limit = max(0, sample_limit)
    comics = (
        db.query(Comic)
        .filter(Comic.library_root_id == selected_root.id)
        .order_by(Comic.relative_path)
        .all()
    )
    existing_relative_paths = {comic.relative_path for comic in comics}

    matched_count = 0
    missing_count = 0
    matched_samples: list[RelocationPathSample] = []
    missing_samples: list[RelocationPathSample] = []

    for comic in comics:
        expected_path = resolve_absolute_path(resolved_proposed_path_str, comic.relative_path)
        if Path(expected_path).is_file():
            matched_count += 1
            _append_sample(matched_samples, comic.relative_path, expected_path, sample_limit)
        else:
            missing_count += 1
            _append_sample(missing_samples, comic.relative_path, expected_path, sample_limit)

    scanned_count = 0
    new_count = 0
    new_samples: list[RelocationPathSample] = []
    supported_extensions = {extension.lower() for extension in settings.supported_extensions}

    try:
        scanned_files = sorted(resolved_proposed_path.rglob("*"), key=lambda path: str(path).lower())
        for scanned_file in scanned_files:
            if not scanned_file.is_file():
                continue
            if scanned_file.suffix.lower() not in supported_extensions:
                continue

            relative_path = compute_relative_path(resolved_proposed_path_str, str(scanned_file))
            if relative_path is None:
                continue

            scanned_count += 1
            if relative_path in existing_relative_paths:
                continue

            new_count += 1
            _append_sample(new_samples, relative_path, scanned_file.as_posix(), sample_limit)
    except OSError as exc:
        raise LibraryRelocationError(f"Relocation path cannot be scanned: {exc}") from exc

    return LibraryRootRelocationPreview(
        library_id=library.id,
        root_id=selected_root.id,
        current_path=selected_root.path,
        proposed_path=resolved_proposed_path_str,
        total_existing=len(comics),
        total_scanned=scanned_count,
        matched_count=matched_count,
        missing_count=missing_count,
        new_count=new_count,
        matched_samples=matched_samples,
        missing_samples=missing_samples,
        new_samples=new_samples,
    )


def confirm_library_root_relocation(
    db: Session,
    *,
    library: Library,
    proposed_path: str,
    root_id: int | None = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> LibraryRootRelocationConfirmation:
    preview = preview_library_root_relocation(
        db,
        library=library,
        proposed_path=proposed_path,
        root_id=root_id,
        sample_limit=sample_limit,
    )
    if preview.confirm_blocked:
        raise LibraryRelocationError(NO_RELOCATION_MATCHES_MESSAGE)

    selected_root = _select_relocation_root(library, preview.root_id)
    selected_root.path = preview.proposed_path

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return LibraryRootRelocationConfirmation(
        preview=preview,
        scan_recommended=True,
        scan_reasons=_scan_reasons(preview),
    )


def _select_relocation_root(library: Library, root_id: int | None) -> LibraryRoot:
    if root_id is not None:
        root = next((candidate for candidate in library.roots if candidate.id == root_id), None)
        if root is None:
            raise LibraryRelocationError("Library root not found")
        return root

    active_roots = [root for root in library.roots if root.is_active]
    if not active_roots:
        raise LibraryRelocationError("Library has no active root to relocate")
    if len(active_roots) > 1:
        raise LibraryRelocationError("root_id is required when a library has multiple active roots")

    return active_roots[0]


def _resolve_proposed_path(proposed_path: str) -> Path:
    if not proposed_path.strip():
        raise LibraryRelocationError("Relocation path is required")

    try:
        resolved_path = Path(proposed_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise LibraryRelocationError("Relocation path must be an existing directory") from exc

    if not resolved_path.is_dir():
        raise LibraryRelocationError("Relocation path must be an existing directory")

    return resolved_path


def _find_overlapping_root(
    db: Session,
    candidate_path: str,
    *,
    exclude_root_id: int | None = None,
) -> LibraryRoot | None:
    query = db.query(LibraryRoot)
    if exclude_root_id is not None:
        query = query.filter(LibraryRoot.id != exclude_root_id)

    for root in query.all():
        if paths_overlap(candidate_path, _comparison_path(root.path)):
            return root

    return None


def _comparison_path(path: str) -> str:
    try:
        return _path_for_storage(Path(path).expanduser().resolve(strict=True))
    except OSError:
        return path.replace("\\", "/")


def _scan_reasons(preview: LibraryRootRelocationPreview) -> list[str]:
    reasons = ["Verify relocated archives and refresh metadata if files changed"]

    if preview.missing_count:
        reasons.append("Reconcile existing comics that were missing at the new root")
    if preview.new_count:
        reasons.append("Import new archive files found at the new root")

    return reasons


def _path_for_storage(path: Path) -> str:
    return path.as_posix()


def _paths_equal(first_path: str, second_path: str) -> bool:
    return (
        compute_relative_path(first_path, second_path) == ""
        and compute_relative_path(second_path, first_path) == ""
    )


def _append_sample(
    samples: list[RelocationPathSample],
    relative_path: str,
    path: str,
    sample_limit: int,
) -> None:
    if len(samples) >= sample_limit:
        return

    samples.append(RelocationPathSample(relative_path=relative_path, path=path))
