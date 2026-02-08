from pathlib import Path

def metadata_worker(file_path) -> dict:
    from app.services.archive import ComicArchive
    from app.services.metadata import parse_comicinfo
    import os

    try:
        path = Path(file_path)

        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)

        with ComicArchive(path) as archive:
            pages = archive.get_pages()
            if not isinstance(pages, list) or len(pages) == 0:
                return {"file_path": file_path, "error": True, "message": "No valid pages found (archive unreadable)"}

            xml = archive.get_comicinfo()

            # MUST have ComicInfo.xml
            if not xml:
                return {
                    "file_path": file_path,
                    "error": True,
                    "message": "Missing ComicInfo.xml"
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
        }

    except Exception as e:
        #print(e)
        return {"file_path": file_path, "error": True, "message": str(e)}