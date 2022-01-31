from functools import update_wrapper
import typing as t

from src.server import exceptions
from src.server.datastructures import MultiDict
from src.server.formparser.multipartparser import MultiPartParser
from src.server.formparser.utils import default_stream_factory
from src.server.http import parse_options_header
from src.server.utils import get_content_length, get_input_stream
from src.server.urls import url_decode_stream


def _exhaust(stream: t.IO[bytes]) -> None:
    bts = stream.read(64 * 1024)
    while bts:
        bts = stream.read(64 * 1024)


F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def exhaust_stream(f: F) -> F:
    """Helper decorator for methods that exhausts the stream on return."""

    def wrapper(self, stream, *args, **kwargs):  # type: ignore
        try:
            return f(self, stream, *args, **kwargs)
        finally:
            exhaust = getattr(stream, "exhaust", None)

            if exhaust is not None:
                exhaust()
            else:
                while True:
                    chunk = stream.read(1024 * 64)

                    if not chunk:
                        break

    return update_wrapper(t.cast(F, wrapper), f)


class FormDataParser:
    """This class implements parsing of form data.
    :param stream_factory: An optional callable that returns a new read and
                           writeable file descriptor.  This callable works
                           the same as :meth:`Response._get_file_stream`.
    :param charset: The character set for URL and url encoded form data.
    :param errors: The encoding error behavior.
    :param max_form_memory_size: the maximum number of bytes to be accepted for
                           in-memory stored form data.  If the data
                           exceeds the value specified an
                           :exc:`~exceptions.RequestEntityTooLarge`
                           exception is raised.
    :param max_content_length: If this is provided and the transmitted data
                               is longer than this value an
                               :exc:`~exceptions.RequestEntityTooLarge`
                               exception is raised.
    :param cls: an optional dict class to use.  If this is not specified
                       or `None` the default :class:`MultiDict` is used.
    """

    def __init__(
            self,
            stream_factory: t.Optional["TStreamFactory"] = None,
            charset: str = "utf-8",
            errors: str = "replace",
            max_form_memory_size: t.Optional[int] = None,
            max_content_length: t.Optional[int] = None,
            cls: t.Optional[t.Type[MultiDict]] = None,
            silent: bool = True,
    ) -> None:
        if stream_factory is None:
            stream_factory = default_stream_factory

        self.stream_factory = stream_factory
        self.charset = charset
        self.errors = errors
        self.max_form_memory_size = max_form_memory_size
        self.max_content_length = max_content_length

        if cls is None:
            cls = MultiDict

        self.cls = cls
        self.silent = silent

    def get_parse_func(
            self, mimetype: str, options: t.Dict[str, str]
    ) -> t.Optional[
        t.Callable[
            ["FormDataParser", t.IO[bytes], str, t.Optional[int], t.Dict[str, str]],
            "t_parse_result",
        ]
    ]:
        return self.parse_functions.get(mimetype)

    def parse_from_environ(self, environ: "WSGIEnvironment"):
        """Parses the information from the environment as form data.
        :param environ: the WSGI environment to be used for parsing.
        :return: A tuple in the form ``(stream, form, files)``.
        """
        content_type = environ.get("CONTENT_TYPE", "")
        content_length = get_content_length(environ)
        mimetype, options = parse_options_header(content_type)
        return self.parse(get_input_stream(environ), mimetype, content_length, options)

    def parse(
            self,
            stream: t.IO[bytes],
            mimetype: str,
            content_length: t.Optional[int],
            options: t.Optional[t.Dict[str, str]] = None,
    ):
        """Parses the information from the given stream, mimetype,
        content length and mimetype parameters.
        :param stream: an input stream
        :param mimetype: the mimetype of the data
        :param content_length: the content length of the incoming data
        :param options: optional mimetype parameters (used for
                        the multipart boundary for instance)
        :return: A tuple in the form ``(stream, form, files)``.
        """
        if (
                self.max_content_length is not None
                and content_length is not None
                and content_length > self.max_content_length
        ):
            # if the input stream is not exhausted, firefox reports Connection Reset
            _exhaust(stream)
            raise exceptions.RequestEntityTooLarge()

        if options is None:
            options = {}

        parse_func = self.get_parse_func(mimetype, options)

        if parse_func is not None:
            try:
                return parse_func(self, stream, mimetype, content_length, options)
            except ValueError:
                if not self.silent:
                    raise

        return stream, self.cls(), self.cls()

    @exhaust_stream
    def _parse_multipart(
            self,
            stream: t.IO[bytes],
            mimetype: str,
            content_length: t.Optional[int],
            options: t.Dict[str, str],
    ):
        parser = MultiPartParser(
            self.stream_factory,
            self.charset,
            self.errors,
            max_form_memory_size=self.max_form_memory_size,
            cls=self.cls,
        )
        boundary = options.get("boundary", "").encode("ascii")

        if not boundary:
            raise ValueError("Missing boundary")

        form, files = parser.parse(stream, boundary, content_length)
        return stream, form, files

    @exhaust_stream
    def _parse_urlencoded(
            self,
            stream: t.IO[bytes],
            mimetype: str,
            content_length: t.Optional[int],
            options: t.Dict[str, str],
    ):
        if (
                self.max_form_memory_size is not None
                and content_length is not None
                and content_length > self.max_form_memory_size
        ):
            # if the input stream is not exhausted, firefox reports Connection Reset
            _exhaust(stream)
            raise exceptions.RequestEntityTooLarge()

        form = url_decode_stream(stream, self.charset, errors=self.errors, cls=self.cls)
        return stream, form, self.cls()

    #: mapping of mimetypes to parsing functions
    parse_functions: t.Dict[
        str,
        t.Callable[
            ["FormDataParser", t.IO[bytes], str, t.Optional[int], t.Dict[str, str]],
            "t_parse_result",
        ],
    ] = {
        "multipart/form-data": _parse_multipart,
        "application/x-www-form-urlencoded": _parse_urlencoded,
        "application/x-url-encoded": _parse_urlencoded,
    }
