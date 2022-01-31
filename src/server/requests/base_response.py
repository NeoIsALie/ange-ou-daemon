from datetime import timezone, timedelta
from http import HTTPStatus

from src.server.datastructures.CallbackDict import CallbackDict
from src.server.datastructures.HeaderSet import HeaderSet
from src.server.datastructures.Header import dump_options_header
from src.server.http import http_date, parse_date, parse_options_header, HTTP_STATUS_CODES, dump_header, \
    parse_set_header
from src.server.properties import header_property
from src.server.urls.urls import _to_str
from src.server.utils import *

_charset_mimetypes = {
    "application/ecmascript",
    "application/javascript",
    "application/sql",
    "application/xml",
    "application/xml-dtd",
    "application/xml-external-parsed-entity",
}


def get_content_type(mimetype: str, charset: str) -> str:
    """Returns the full content type string with charset for a mimetype.
    :param mimetype: The mimetype to be used as content type.
    :param charset: The charset to be appended for text mimetypes.
    :return: The content type.
    """
    if (
            mimetype.startswith("text/")
            or mimetype in _charset_mimetypes
            or mimetype.endswith("+xml")
    ):
        mimetype += f"; charset={charset}"

    return mimetype


def _set_property(name: str, doc: t.Optional[str] = None) -> property:
    def fget(self: "Response") -> HeaderSet:
        def on_update(header_set: HeaderSet) -> None:
            if not header_set and name in self.headers:
                del self.headers[name]
            elif header_set:
                self.headers[name] = header_set.to_header()

        return parse_set_header(self.headers.get(name), on_update)

    def fset(
            self: "Response",
            value: t.Optional[
                t.Union[str, t.Dict[str, t.Union[str, int]], t.Iterable[str]]
            ],
    ) -> None:
        if not value:
            del self.headers[name]
        elif isinstance(value, str):
            self.headers[name] = value
        else:
            self.headers[name] = dump_header(value)

    return property(fget, fset, doc=doc)


class Response:
    """Represents the non-IO parts of an HTTP response, specifically the
    status and headers but not the body.
    :param status: The status code for the response. Either an int, in
        which case the default status message is added, or a string in
        the form ``{code} {message}``, like ``404 Not Found``. Defaults
        to 200.
    :param headers: A :class:`datastructures.Headers` object,
        or a list of ``(key, value)`` tuples that will be converted to a
        ``Headers`` object.
    :param mimetype: The mime type (content type without charset or
        other parameters) of the response. If the value starts with
        ``text/`` (or matches some other special cases), the charset
        will be added to create the ``content_type``.
    :param content_type: The full content type of the response.
        Overrides building the value from ``mimetype``.
    """

    #: the charset of the response.
    charset = "utf-8"

    #: the default status if none is provided.
    default_status = 200

    #: the default mimetype if none is provided.
    default_mimetype = "text/plain"

    max_cookie_size = 4093

    # A :class:`Headers` object representing the response headers.
    headers: Headers

    def __init__(
            self,
            status: t.Optional[t.Union[int, str, HTTPStatus]] = None,
            headers: t.Optional[
                t.Union[
                    t.Mapping[str, t.Union[str, int, t.Iterable[t.Union[str, int]]]],
                    t.Iterable[t.Tuple[str, t.Union[str, int]]],
                ]
            ] = None,
            mimetype: t.Optional[str] = None,
            content_type: t.Optional[str] = None,
    ) -> None:
        if isinstance(headers, Headers):
            self.headers = headers
        elif not headers:
            self.headers = Headers()
        else:
            self.headers = Headers(headers)

        if content_type is None:
            if mimetype is None and "content-type" not in self.headers:
                mimetype = self.default_mimetype
            if mimetype is not None:
                mimetype = get_content_type(mimetype, self.charset)
            content_type = mimetype
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        if status is None:
            status = self.default_status
        self.status = status  # type: ignore

    def __repr__(self) -> str:
        return f"<{type(self).__name__} [{self.status}]>"

    @property
    def status_code(self) -> int:
        """The HTTP status code as a number."""
        return self._status_code

    @status_code.setter
    def status_code(self, code: int) -> None:
        self.status = code  # type: ignore

    @property
    def status(self) -> str:
        """The HTTP status code as a string."""
        return self._status

    @status.setter
    def status(self, value: t.Union[str, int, HTTPStatus]) -> None:
        if not isinstance(value, (str, bytes, int, HTTPStatus)):
            raise TypeError("Invalid status argument")

        self._status, self._status_code = self._clean_status(value)

    def _clean_status(self, value: t.Union[str, int, HTTPStatus]) -> t.Tuple[str, int]:
        if isinstance(value, HTTPStatus):
            value = int(value)
        status = _to_str(value, self.charset)
        split_status = status.split(None, 1)

        if len(split_status) == 0:
            raise ValueError("Empty status argument")

        if len(split_status) > 1:
            if split_status[0].isdigit():
                # code and message
                return status, int(split_status[0])

            # multi-word message
            return f"0 {status}", 0

        if split_status[0].isdigit():
            # code only
            status_code = int(split_status[0])

            try:
                status = f"{status_code} {HTTP_STATUS_CODES[status_code].upper()}"
            except KeyError:
                status = f"{status_code} UNKNOWN"

            return status, status_code

        # one-word message
        return f"0 {status}", 0

    @property
    def is_json(self) -> bool:
        """Check if the mimetype indicates JSON data, either
        :mimetype:`application/json` or :mimetype:`application/*+json`.
        """
        mt = self.mimetype
        return mt is not None and (
                mt == "application/json"
                or mt.startswith("application/")
                and mt.endswith("+json")
        )

    # Common Descriptors

    @property
    def mimetype(self) -> t.Optional[str]:
        """The mimetype (content type without charset etc.)"""
        ct = self.headers.get("content-type")

        if ct:
            return ct.split(";")[0].strip()
        else:
            return None

    @mimetype.setter
    def mimetype(self, value: str) -> None:
        self.headers["Content-Type"] = get_content_type(value, self.charset)

    @property
    def mimetype_params(self) -> t.Dict[str, str]:
        """The mimetype parameters as dict. """

        def on_update(d: CallbackDict) -> None:
            self.headers["Content-Type"] = dump_options_header(self.mimetype, d)

        d = parse_options_header(self.headers.get("content-type", ""))[1]
        return CallbackDict(d, on_update)

    location = header_property[str](
        "Location",
        doc="""The Location response-header field is used to redirect
        the recipient to a location other than the Request-URI for
        completion of the request or identification of a new
        resource.""",
    )

    content_type = header_property[str](
        "Content-Type",
        doc="""The Content-Type entity-header field indicates the media
        type of the entity-body sent to the recipient or, in the case of
        the HEAD method, the media type that would have been sent had
        the request been a GET.""",
    )
    content_length = header_property(
        "Content-Length",
        None,
        int,
        str,
        doc="""The Content-Length entity-header field indicates the size
        of the entity-body, in decimal number of OCTETs, sent to the
        recipient or, in the case of the HEAD method, the size of the
        entity-body that would have been sent had the request been a
        GET.""",
    )
    content_location = header_property[str](
        "Content-Location",
        doc="""The Content-Location entity-header field MAY be used to
        supply the resource location for the entity enclosed in the
        message when that entity is accessible from a location separate
        from the requested resource's URI.""",
    )
    content_encoding = header_property[str](
        "Content-Encoding",
        doc="""The Content-Encoding entity-header field is used as a
        modifier to the media-type. When present, its value indicates
        what additional content codings have been applied to the
        entity-body, and thus what decoding mechanisms must be applied
        in order to obtain the media-type referenced by the Content-Type
        header field.""",
    )
    date = header_property(
        "Date",
        None,
        parse_date,
        http_date,
        doc="""The Date general-header field represents the date and
        time at which the message was originated, having the same
        semantics as orig-date in RFC 822.
        """,
    )
