import logging

logger = logging.getLogger(__name__)

try:
    import pillow_jxl  # noqa: F401
except ImportError:
    logger.debug("pillow-jxl-plugin not installed; JPEG XL support unavailable.")
