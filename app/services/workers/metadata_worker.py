def metadata_worker(file_path: str) -> dict:
    from app.services.archive import ComicArchive
    from app.services.metadata import parse_comicinfo
    import os

    try:
        mtime = os.path.getmtime(file_path)
        size = os.path.getsize(file_path)

        with ComicArchive(file_path) as archive:
            pages = archive.get_pages()
            if not pages:
                return {"file_path": file_path, "error": True, "message": "No pages"}

            xml = archive.get_comicinfo()

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
        return {"file_path": file_path, "error": True, "message": str(e)}