"""Document parser registry for the Knowledge Base.

Usage:
    from knowledge.parsers import get_parser
    parser = get_parser()          # auto — uses Marker by default
    result = parser.parse(path)    # {"markdown": ..., "pages": [...], "metadata": {...}}
"""

from knowledge.parsers.base import get_parser, BaseParser, ParseResult, PageInfo

__all__ = ["get_parser", "BaseParser", "ParseResult", "PageInfo"]
