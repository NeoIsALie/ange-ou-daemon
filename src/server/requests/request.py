import typing as t

from src.server.datastructures.Header import Headers
from src.server.datastructures.ImmutableMultiDict import ImmutableMultiDict
from src.server.datastructures.MultiDict import MultiDict
from src.server.http import parse_date
from src.server.http import parse_options_header
from src.server.urls.urls import url_decode, _to_str
from src.server.utils import cached_property
from src.server.properties import header_property
from src.server.urls.utils import get_current_url
from src.server.urls.utils import get_host


class Request:
    """Represents the non-IO parts of a HTTP request, including the
    method, URL info, and headers.
    :param method: The method the request was made with, such as
        ``GET``.
    :param scheme: The URL scheme of the protocol the request used, such
        as ``https`` or ``wss``.
    :param server: The address of the server. ``(host, port)``,
        ``(path, None)`` for unix sockets, or ``None`` if not known.
    :param root_path: The prefix that the application is mounted under.
        This is prepended to generated URLs, but is not part of route
        matching.
    :param path: The path part of the URL after ``root_path``.
    :param query_string: The part of the URL after the "?".
    :param headers: The headers received with the request.
    :param remote_addr: The address of the client sending the request.

    """

    charset = "utf-8"
    encoding_errors = "replace"
    parameter_storage_class: t.Type[MultiDict] = ImmutableMultiDict

    trusted_hosts: t.Optional[t.List[str]] = None

    def __init__(
        self,
        method: str,
        scheme: str,
        server: t.Optional[t.Tuple[str, t.Optional[int]]],
        root_path: str,
        path: str,
        query_string: bytes,
        headers: Headers,
        remote_addr: t.Optional[str],
    ) -> None:
        #: The method the request was made with, such as ``GET``.
        self.method = method.upper()
        #: The URL scheme of the protocol the request used, such as
        #: ``https`` or ``wss``.
        self.scheme = scheme
        #: The address of the server. ``(host, port)``, ``(path, None)``
        #: for unix sockets, or ``None`` if not known.
        self.server = server
        #: The prefix that the application is mounted under, without a
        #: trailing slash. :attr:`path` comes after this.
        self.root_path = root_path.rstrip("/")
        #: The path part of the URL after :attr:`root_path`. This is the
        #: path used for routing within the application.
        self.path = "/" + path.lstrip("/")
        #: The part of the URL after the "?". This is the raw value, use
        #: :attr:`args` for the parsed values.
        self.query_string = query_string
        #: The headers received with the request.
        self.headers = headers
        #: The address of the client sending the request.
        self.remote_addr = remote_addr

    def __repr__(self) -> str:
        try:
            url = self.url
        except Exception as e:
            url = f"(invalid URL: {e})"

        return f"<{type(self).__name__} {url!r} [{self.method}]>"

    @property
    def url_charset(self) -> str:
        """The charset that is assumed for URLs."""
        return self.charset

    @cached_property
    def args(self) -> "MultiDict[str, str]":
        """The parsed URL parameters"""
        return url_decode(
            self.query_string,
            self.url_charset,
            errors=self.encoding_errors,
            cls=self.parameter_storage_class,
        )

    @cached_property
    def full_path(self) -> str:
        """Requested path, including the query string."""
        return f"{self.path}?{_to_str(self.query_string, self.url_charset)}"

    @cached_property
    def url(self) -> str:
        """The full request URL with the scheme, host, root path, path,
        and query string."""
        return get_current_url(
            self.scheme, self.host, self.root_path, self.path, self.query_string
        )

    @cached_property
    def base_url(self) -> str:
        return get_current_url(self.scheme, self.host, self.root_path, self.path)

    @cached_property
    def root_url(self) -> str:
        """The request URL scheme, host, and root path. This is the root
        that the application is accessed from.
        """
        return get_current_url(self.scheme, self.host, self.root_path)

    @cached_property
    def host_url(self) -> str:
        """The request URL scheme and host only."""
        return get_current_url(self.scheme, self.host)

    @cached_property
    def host(self) -> str:
        """The host name the request was made to, including the port if
        it's non-standard.
        """
        return get_host(
            self.scheme, self.headers.get("host"), self.server, self.trusted_hosts
        )

    content_type = header_property[str](
        "Content-Type",
        doc="""The Content-Type entity-header field indicates the media
        type of the entity-body sent to the recipient or, in the case of
        the HEAD method, the media type that would have been sent had
        the request been a GET.""",
        read_only=True,
    )

    @cached_property
    def content_length(self) -> t.Optional[int]:
        """The Content-Length entity-header field indicates the size of the
        entity-body in bytes or, in the case of the HEAD method, the size of
        the entity-body that would have been sent had the request been a
        GET.
        """
        if self.headers.get("Transfer-Encoding", "") == "chunked":
            return None

        content_length = self.headers.get("Content-Length")
        if content_length is not None:
            try:
                return max(0, int(content_length))
            except (ValueError, TypeError):
                pass

        return None

    content_encoding = header_property[str](
        "Content-Encoding",
        doc="""The Content-Encoding entity-header field is used as a
        modifier to the media-type. When present, its value indicates
        what additional content codings have been applied to the
        entity-body, and thus what decoding mechanisms must be applied
        in order to obtain the media-type referenced by the Content-Type
        header field.""",
        read_only=True,
    )
    referrer = header_property[str](
        "Referer",
        doc="""The Referer[sic] request-header field allows the client
        to specify, for the server's benefit, the address (URI) of the
        resource from which the Request-URI was obtained (the
        "referrer", although the header field is misspelled).""",
        read_only=True,
    )
    date = header_property(
        "Date",
        None,
        parse_date,
        doc="""The Date general-header field represents the date and
        time at which the message was originated by RFC 822.
        """,
        read_only=True,
    )

    def _parse_content_type(self) -> None:
        if not hasattr(self, "_parsed_content_type"):
            self._parsed_content_type = parse_options_header(
                self.headers.get("Content-Type", "")
            )

    @property
    def mimetype(self) -> str:
        self._parse_content_type()
        return self._parsed_content_type[0].lower()

    @property
    def mimetype_params(self) -> t.Dict[str, str]:
        self._parse_content_type()
        return self._parsed_content_type[1]
