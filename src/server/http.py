from datetime import timezone, datetime, date, time
from time import struct_time, mktime
from urllib.parse import unquote_to_bytes as _unquote
from urllib.request import parse_http_list as _parse_list_header
import typing as t
import email.utils
import re

from src.server.datastructures.Header import quote_header_value
from src.server.datastructures.HeaderSet import HeaderSet

HTTP_STATUS_CODES = {
    100: "Continue",
    101: "Switching Protocols",
    102: "Processing",
    103: "Early Hints",  # see RFC 8297
    200: "OK",
    201: "Created",
    202: "Accepted",
    203: "Non Authoritative Information",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content",
    207: "Multi Status",
    208: "Already Reported",  # see RFC 5842
    226: "IM Used",  # see RFC 3229
    300: "Multiple Choices",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    305: "Use Proxy",
    306: "Switch Proxy",  # unused
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",  # unused
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Request Entity Too Large",
    414: "Request URI Too Long",
    415: "Unsupported Media Type",
    416: "Requested Range Not Satisfiable",
    417: "Expectation Failed",
    418: "I'm a teapot",  # see RFC 2324
    421: "Misdirected Request",  # see RFC 7540
    422: "Unprocessable Entity",
    423: "Locked",
    424: "Failed Dependency",
    425: "Too Early",  # see RFC 8470
    426: "Upgrade Required",
    428: "Precondition Required",  # see RFC 6585
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    449: "Retry With",  # proprietary MS extension
    451: "Unavailable For Legal Reasons",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
    505: "HTTP Version Not Supported",
    506: "Variant Also Negotiates",  # see RFC 2295
    507: "Insufficient Storage",
    508: "Loop Detected",  # see RFC 5842
    510: "Not Extended",
    511: "Network Authentication Failed",
}

_option_header_start_mime_type = re.compile(r",\s*([^;,\s]+)([;,]\s*.+)?")

_option_header_piece_re = re.compile(
    r"""
    ;\s*,?\s*  # newlines were replaced with commas
    (?P<key>
        "[^"\\]*(?:\\.[^"\\]*)*"  # quoted string
    |
        [^\s;,=*]+  # token
    )
    (?:\*(?P<count>\d+))?  # *1, optional continuation index
    \s*
    (?:  # optionally followed by =value
        (?:  # equals sign, possibly with encoding
            \*\s*=\s*  # * indicates extended notation
            (?:  # optional encoding
                (?P<encoding>[^\s]+?)
                '(?P<language>[^\s]*?)'
            )?
        |
            =\s*  # basic notation
        )
        (?P<value>
            "[^"\\]*(?:\\.[^"\\]*)*"  # quoted string
        |
            [^;,]+  # token
        )?
    )?
    \s*
    """,
    flags=re.VERBOSE,
)


@t.overload
def _dt_as_utc(dt: None) -> None:
    ...


@t.overload
def _dt_as_utc(dt: datetime) -> datetime:
    ...


def _dt_as_utc(dt: t.Optional[datetime]) -> t.Optional[datetime]:
    if dt is None:
        return dt

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo != timezone.utc:
        return dt.astimezone(timezone.utc)

    return dt


def http_date(
        timestamp: t.Optional[t.Union[datetime, date, int, float, struct_time]] = None
) -> str:
    """Format a datetime object or timestamp into an :rfc:`2822` date
    string.
    :param timestamp: The datetime or timestamp to format. Defaults to
        the current time.
    """
    if isinstance(timestamp, date):
        if not isinstance(timestamp, datetime):
            # Assume plain date is midnight UTC.
            timestamp = datetime.combine(timestamp, time(), tzinfo=timezone.utc)
        else:
            # Ensure datetime is timezone-aware.
            timestamp = _dt_as_utc(timestamp)

        return email.utils.format_datetime(timestamp, usegmt=True)

    if isinstance(timestamp, struct_time):
        timestamp = mktime(timestamp)

    return email.utils.formatdate(timestamp, usegmt=True)


def unquote_header_value(value: str, is_filename: bool = False) -> str:
    r"""Unquotes a header value.  (Reversal of :func:`quote_header_value`).
    This does not use the real unquoting but what browsers are actually
    using for quoting.
    :param value: the header value to unquote.
    :param is_filename: The value represents a filename or path.
    """
    if value and value[0] == value[-1] == '"':
        value = value[1:-1]
        if not is_filename or value[:2] != "\\\\":
            return value.replace("\\\\", "\\").replace('\\"', '"')
    return value


@t.overload
def parse_options_header(
        value: t.Optional[str], multiple: "te.Literal[False]" = False
) -> t.Tuple[str, t.Dict[str, str]]:
    ...


@t.overload
def parse_options_header(
        value: t.Optional[str], multiple: "te.Literal[True]"
) -> t.Tuple[t.Any, ...]:
    ...


def parse_options_header(
        value: t.Optional[str], multiple: bool = False
) -> t.Union[t.Tuple[str, t.Dict[str, str]], t.Tuple[t.Any, ...]]:
    if not value:
        return "", {}

    result: t.List[t.Any] = []

    value = "," + value.replace("\n", ",")
    while value:
        match = _option_header_start_mime_type.match(value)
        if not match:
            break
        result.append(match.group(1))  # mimetype
        options: t.Dict[str, str] = {}
        # Parse options
        rest = match.group(2)
        encoding: t.Optional[str]
        continued_encoding: t.Optional[str] = None
        while rest:
            optmatch = _option_header_piece_re.match(rest)
            if not optmatch:
                break
            option, count, encoding, language, option_value = optmatch.groups()
            # Continuations don't have to supply the encoding after the
            # first line. If we're in a continuation, track the current
            # encoding to use for subsequent lines. Reset it when the
            # continuation ends.
            if not count:
                continued_encoding = None
            else:
                if not encoding:
                    encoding = continued_encoding
                continued_encoding = encoding
            option = unquote_header_value(option)

            if option_value is not None:
                option_value = unquote_header_value(option_value, option == "filename")

                if encoding is not None:
                    option_value = _unquote(option_value).decode(encoding)

            if count:
                # Continuations append to the existing value. For
                # simplicity, this ignores the possibility of
                # out-of-order indices, which shouldn't happen anyway.
                if option_value is not None:
                    options[option] = options.get(option, "") + option_value
            else:
                options[option] = option_value

            rest = rest[optmatch.end():]
        result.append(options)
        if multiple is False:
            return tuple(result)
        value = rest

    return tuple(result) if result else ("", {})


def parse_date(value: t.Optional[str]) -> t.Optional[datetime]:
    if value is None:
        return None

    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt


def dump_header(
        iterable: t.Union[t.Dict[str, t.Union[str, int]], t.Iterable[str]],
        allow_token: bool = True,
) -> str:
    if isinstance(iterable, dict):
        items = []
        for key, value in iterable.items():
            if value is None:
                items.append(key)
            else:
                items.append(
                    f"{key}={quote_header_value(value, allow_token=allow_token)}"
                )
    else:
        items = [quote_header_value(x, allow_token=allow_token) for x in iterable]
    return ", ".join(items)


def parse_set_header(
        value: t.Optional[str],
        on_update: t.Optional[t.Callable[["ds.HeaderSet"], None]] = None,
) -> "ds.HeaderSet":
    """Parse a set-like header and return a
    HeaderSet(['token', 'quoted value'])
    To create a header from the :class:`HeaderSet` again, use the
    :func:`dump_header` function.
    :param value: a set header to be parsed.
    :param on_update: an optional callable that is called every time a
                      value on the :class:`~datastructures.HeaderSet`
                      object is changed.
    :return: a :class:`~datastructures.HeaderSet`
    """
    if not value:
        return HeaderSet(None, on_update)
    return HeaderSet(parse_list_header(value), on_update)


def parse_list_header(value: str) -> t.List[str]:
    """Parse lists as described by RFC 2068 Section 2."""
    result = []
    for item in _parse_list_header(value):
        if item[:1] == item[-1:] == '"':
            item = unquote_header_value(item[1:-1])
        result.append(item)
    return result
