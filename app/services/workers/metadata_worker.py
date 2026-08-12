from pathlib import Path

def _worker_context(file_input) -> tuple[str, dict]:
    if isinstance(file_input, dict):
        file_path = file_input["file_path"]
        context = {
            key: file_input[key]
            for key in ("library_root_id", "library_root_path")
            if key in file_input
        }
        return file_path, context

    return file_input, {}


def metadata_worker(file_input) -> dict:
    from app.services.archive import ComicArchive
    from app.services.metadata import parse_comicinfo
    import os

    file_path, context = _worker_context(file_input)

    try:
        path = Path(file_path)

        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)

        with ComicArchive(path) as archive:
            pages = archive.get_pages()
            if not isinstance(pages, list) or len(pages) == 0:
                return {
                    "file_path": file_path,
                    "error": True,
                    "message": "No valid pages found (archive unreadable)",
                    **context,
                }

            xml = archive.get_comicinfo()

            # MUST have ComicInfo.xml
            if not xml:
                return {
                    "file_path": file_path,
                    "error": True,
                    "message": "Missing ComicInfo.xml",
                    **context,
                }

            # 1. Establish Physical Truth of page count
            physical_count = len(pages)
            metadata = {'page_count': physical_count}

            if xml:
                parsed = parse_comicinfo(xml)
                metadata.update(parsed)

                # Force overwrite: Always use physical count for this field.
                # We trust the file system over the XML tag for navigational safety in the reader.
                metadata['page_count'] = physical_count

                metadata["raw_metadata"] = parsed

        return {
            "file_path": file_path,
            "mtime": mtime,
            "size": size,
            "metadata": metadata,
            "error": False,
            **context,
        }

    except Exception as e:
        #print(e)
        return {"file_path": file_path, "error": True, "message": str(e), **context}
