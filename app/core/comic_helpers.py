from sqlalchemy import func, or_
from app.models.comic import Comic

# Centralized list of non-standard formats
NON_PLAIN_FORMATS = [
    'annual',
    'giant size',
    'giant-size',
    'graphic novel',
    'one shot',
    'one-shot',
    'hardcover',
    'trade paperback',
    'trade paper back',
    'tpb',
    'preview',
    'special'
]

def get_format_filters():
    """
    Returns SQL expressions to categorize comics.
    Usage: is_plain, is_annual, is_special = get_format_filters()
    """
    is_plain = or_(
        Comic.format == None,
        func.lower(Comic.format).not_in(NON_PLAIN_FORMATS)
    )

    is_annual = func.lower(Comic.format) == 'annual'

    is_special = (func.lower(Comic.format) != 'annual') & \
                 (func.lower(Comic.format).in_(NON_PLAIN_FORMATS))

    return is_plain, is_annual, is_special


def get_smart_cover(base_query):
    """
    Given a base query (filtered by series or volume), find the best cover.
    Priority:
    1. Plain Issue (not Annual/Special) AND Not Issue #0
    2. Fallback: First issue by Year/Number
    """
    is_plain, _, _ = get_format_filters()

    # 1. Try to find a standard issue (No #0, No Annuals)
    cover = base_query.filter(is_plain) \
        .filter(Comic.number != '0') \
        .order_by(Comic.year, Comic.number) \
        .first()

    if cover:
        return cover

    # 2. Fallback: Just give me the first thing you have (Annuals, #0, etc)
    return base_query.order_by(Comic.year, Comic.number).first()