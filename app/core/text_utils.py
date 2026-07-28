import re


def normalize_title(text: str) -> str:
    """
    Normalize a title/series/event name for loose matching:
    'The Infinity Gauntlet' -> 'infinity gauntlet'
    """
    if not text:
        return ""
    text = text.lower()
    if text.startswith("the "):
        text = text[4:]
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return text.strip()
