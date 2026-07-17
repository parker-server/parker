import logging
from pathlib import Path
from typing import Optional, Tuple, Annotated, Dict
from io import BytesIO
from PIL import Image, ImageFilter, ImageOps
from colorthief import ColorThief


from app.services.archive import ComicArchive
from app.config import settings


class ImageService:
    """Service for extracting and processing comic images"""

    def __init__(self):
        self.thumbnail_size: tuple[float, float] = settings.thumbnail_size
        self.avatar_size: tuple[float, float] = settings.avatar_size

    def process_cover(self, comic_path: str, thumbnail_path: Path) -> dict:
        """
        Optimized Workflow:
        1. Open Archive (Expensive I/O) -> Extract Cover
        2. Calculate Colors (CPU) using a small resized copy
        3. Resize & Save Thumbnail (CPU/Disk) using the original high-res data

        Returns: { "success": bool, "palette": dict }
        """
        result = {"success": False, "palette": None}

        try:
            # 1. Get Raw Bytes (Reuse existing logic, force raw)
            # This handles the archive opening and file detection
            cover_bytes, success, _ = self.get_page_image(comic_path, 0, transcode_webp=False)

            if not success or not cover_bytes:
                return result

            # 2. Load into Pillow
            img = Image.open(BytesIO(cover_bytes))
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # 3. Extract Colors (Run ColorThief on a small copy)
            # Optimization: Resizing to 150px makes ColorThief 10x faster with 99% accuracy
            small_img = img.copy()
            small_img.thumbnail((150, 150))

            # ColorThief needs a file-like object
            small_bytes = BytesIO()
            small_img.save(small_bytes, format='JPEG')

            color_thief = ColorThief(small_bytes)
            # Get 5 colors
            raw_palette = color_thief.get_palette(color_count=5, quality=10)

            def rgb_to_hex(rgb):
                return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

            result['palette'] = {
                'primary': rgb_to_hex(raw_palette[0]),
                'secondary': rgb_to_hex(raw_palette[1]),
                'accent1': rgb_to_hex(raw_palette[2]),
                'accent2': rgb_to_hex(raw_palette[3]) if len(raw_palette) > 3 else None,
                'accent3': rgb_to_hex(raw_palette[4]) if len(raw_palette) > 4 else None
            }

            # 4. Generate Thumbnail (Resize the original high-res img)
            # We do this LAST so we don't accidentally use the tiny 150px image
            width, height = self.thumbnail_size
            img.thumbnail((width, height), Image.Resampling.LANCZOS)

            # Save
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(thumbnail_path, format='WEBP', quality=85)

            result['success'] = True
            return result

        except Exception as e:
            print(f"Error processing cover for {Path(comic_path).name}: {e}")
            return result


    def get_page_image(self, comic_path: str, page_index: int,
                       sharpen: bool = False,
                       grayscale: bool = False,
                       transcode_webp: bool = False
                       ) -> Tuple[Optional[bytes], bool, str]:
        """
        Extract a specific page from a comic archive, optionally applying filters.

        Args:
            comic_path: Path to the comic file
            page_index: Zero-based page index
            sharpen: Whether to sharpen the image
            grayscale: Whether to apply grayscale filters
            transcode_webp: Whether to convert the output to WebP (if large)

        Returns:
            (bytes, success, mimetype)
        """
        try:
            file_path = Path(comic_path)

            if not file_path.exists():
                print(f"Comic file not found: {comic_path}")
                return None, False, "application/octet-stream"

            with ComicArchive(file_path) as archive:

                pages = archive.get_pages()

                if page_index < 0 or page_index >= len(pages):
                    print(f"Page index {page_index} out of range (0-{len(pages) - 1})")
                    return None, False, "application/octet-stream"

                # Extract Raw Bytes
                image_bytes = archive.read_file(pages[page_index])
                original_size = len(image_bytes)

                # Detect original mime type based on file extension in archive
                # (Simple heuristic is enough here, or use python-magic if you want to be strict)
                filename = pages[page_index].lower()
                mime_type = "image/jpeg"  # Default
                if filename.endswith(".png"): mime_type = "image/png"
                elif filename.endswith(".webp"): mime_type = "image/webp"
                elif filename.endswith(".gif"): mime_type = "image/gif"
                elif filename.endswith(".jxl"): mime_type = "image/jxl"
                elif filename.endswith(".avif"): mime_type = "image/avif"

                # Logic: Should we Transcode?
                # Only if requested AND image is large (>500KB) AND not already WebP
                needs_transcode = transcode_webp and original_size > 500_000 and mime_type != "image/webp"

                # FAST PATH: If no processing needed, return raw bytes
                if not sharpen and not grayscale and not needs_transcode:
                    return image_bytes, True, mime_type

                # SLOW PATH: Pillow Processing
                try:
                    img = Image.open(BytesIO(image_bytes))

                    # Convert to RGB (Strip Alpha/Palette if transcoding to optimize size)
                    # For WebP, RGBA is fine, but for Grayscale we need L.
                    if img.mode not in ('RGB', 'L', 'RGBA'):
                        img = img.convert('RGB')

                    # 2. OPTIMIZATION: Resize Huge Images
                    # If we are transcoding for bandwidth/speed, we shouldn't serve 4000px images.
                    # 2560px is more than enough for iPad Pros/Tablets.
                    if transcode_webp:
                        max_dimension = 2560
                        if img.width > max_dimension or img.height > max_dimension:
                            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

                    # A. Apply Grayscale
                    if grayscale:
                        img = ImageOps.grayscale(img)

                    # B. Apply Sharpening (UnsharpMask is best for scans)
                    if sharpen:
                        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

                    # 4. Save / Transcode
                    output = BytesIO()

                    if needs_transcode or mime_type == "image/webp":
                        # Encode fast (The biggest latency saver)
                        # quality=75: Good visual fidelity, low file size
                        # method=0: Fastest encoding speed
                        img.save(output, format="WEBP", quality=75, method=0)
                        return output.getvalue(), True, "image/webp"
                    else:
                        # Fallback to JPEG if we just sharpened but didn't ask for WebP
                        img.save(output, format="JPEG", quality=85)
                        return output.getvalue(), True, "image/jpeg"

                except Exception as e:
                    logging.error(f"Image processing failed: {e}")
                    print(f"Error processing image: {e}")
                    # CRITICAL: Return original bytes, but flag as FAILED processing
                    # so the controller knows not to cache this as the 'filtered' version.
                    return image_bytes, False, mime_type  # Fallback, just return original bytes

        except Exception as e:
            print(f"Error extracting page {page_index}: {e}")
            return None, False, "application/octet-stream"

    @staticmethod
    def get_page_count(comic_path: str) -> int:
        """Get the number of pages in a comic"""
        try:
            file_path = Path(comic_path)
            if not file_path.exists():
                return 0
            with ComicArchive(file_path) as archive:
                return len(archive.get_pages())
        except Exception:
            return 0


    def process_avatar(self, image_data: bytes, output_path: Path) -> bool:
        """
        Process a raw avatar upload:
        1. Fix Orientation (EXIF)
        2. Normalize Color (RGB/RGBA)
        3. Resize to standard avatar size
        4. Save as WebP
        """
        try:
            img = Image.open(BytesIO(image_data))

            # 1. Fix Orientation (Phone selfies often have rotation flags)
            img = ImageOps.exif_transpose(img)

            # 2. Convert to RGB/RGBA (Handle PNGs, BMPs, etc)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

            # 3. Resize (Maintain Aspect Ratio)
            img.thumbnail(self.avatar_size, Image.Resampling.LANCZOS)

            # 4. Save
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "WEBP", quality=85)

            return True
        except Exception as e:
            logging.error(f"Avatar processing error: {e}")
            return False

    def extract_palette(self, comic_path: str, num_colors=5) -> Optional[Dict[str, str]]:
        """Extract color palette using ColorThief"""
        try:

            path = Path(comic_path)
            if not path.exists():
                print(f"Image file not found: {path}")
                return None

            # 1. Get Cover Bytes
            cover_bytes, success, _ = self.get_page_image(comic_path, 0, transcode_webp=False)
            if not success or not cover_bytes:
                return None

            color_thief = ColorThief(BytesIO(cover_bytes))
            palette = color_thief.get_palette(color_count=num_colors, quality=10)

            # Convert to HEX
            def rgb_to_hex(rgb_tuple):
                return f"#{rgb_tuple[0]:02x}{rgb_tuple[1]:02x}{rgb_tuple[2]:02x}"

            return {
                'primary': rgb_to_hex(palette[0]),
                'secondary': rgb_to_hex(palette[1]),
                'accent1': rgb_to_hex(palette[2]),
                'accent2': rgb_to_hex(palette[3]),
                'accent3': rgb_to_hex(palette[4]) if len(palette) > 4 else None
            }

        except Exception as e:
            print(f"Color palette extraction failed for {comic_path}: {e}")
            return None


