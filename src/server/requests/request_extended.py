import functools
import typing
import typing as t
from io import BytesIO

from src.server.requests.request import Request as RequestBase
from src.server.datastructures import CombinedMultiDict
from src.server.datastructures.EnvironHeaders import EnvironHeaders
from src.server.datastructures.FileStorage import FileStorage
from src.server.datastructures.ImmutableMultiDict import ImmutableMultiDict
from src.server.datastructures.MultiDict import iter_multi_items, MultiDict
from src.server.formparser.formparser import FormDataParser, default_stream_factory
from src.server.utils import cached_property, _get_server, _wsgi_decoding_dance, get_input_stream
from src.server.exceptions import HTTPException


class Request(RequestBase):
    """Represents an incoming WSGI HTTP request, with headers and body.
    :param environ: The WSGI environ is generated by the WSGI server and
        contains information about the server configuration and client
        request.
    :param populate_request: Add this request object to the WSGI environ.
    :param shallow: Makes reading from :attr:`stream` (and any method
        that would read from it) raise a :exc:`RuntimeError`. Useful to
        prevent consuming the form data in middleware, which would make
        it unavailable to the final application.
    """

    #: the maximum content length.
    max_content_length: t.Optional[int] = None

    #: the maximum form field size.
    max_form_memory_size: t.Optional[int] = None

    #: The form data parser that shoud be used.
    form_data_parser_class: t.Type[FormDataParser] = FormDataParser

    environ: "WSGIEnvironment"

    shallow: bool

    def __init__(
        self,
        environ: "WSGIEnvironment",
        populate_request: bool = True,
        shallow: bool = False,
    ) -> None:
        super().__init__(
            method=environ.get("REQUEST_METHOD", "GET"),
            scheme=environ.get("wsgi.url_scheme", "http"),
            server=_get_server(environ),
            root_path=_wsgi_decoding_dance(
                environ.get("SCRIPT_NAME") or "", self.charset, self.encoding_errors
            ),
            path=_wsgi_decoding_dance(
                environ.get("PATH_INFO") or "", self.charset, self.encoding_errors
            ),
            query_string=environ.get("QUERY_STRING", "").encode("latin1"),
            headers=EnvironHeaders(environ),
            remote_addr=environ.get("REMOTE_ADDR"),
        )
        self.environ = environ
        self.shallow = shallow

        if populate_request and not shallow:
            self.environ["request"] = self

    @classmethod
    def from_values(cls, *args: t.Any, **kwargs: t.Any) -> "Request":
        """Create a new request object based on the values provided.  If
        environ is given missing values are filled from there.
        :return: request object
        """
        from src.server.datastructures.EnvironBuilder import EnvironBuilder

        charset = kwargs.pop("charset", cls.charset)
        kwargs["charset"] = charset
        builder = EnvironBuilder(*args, **kwargs)
        try:
            return builder.get_request(cls)
        finally:
            builder.close()

    @classmethod
    def application(
        cls, f: t.Callable[["Request"], "WSGIApplication"]
    ) -> "WSGIApplication":
        """Decorate a function as responder that accepts the request as
        the last argument.
        :param f: the WSGI callable to decorate
        :return: a new WSGI callable
        """

        @functools.wraps(f)
        def application(*args):  # type: ignore
            request = cls(args[-2])
            with request:
                try:
                    resp = f(*args[:-2] + (request,))
                except HTTPException as e:
                    resp = e.get_response(args[-2])
                return resp(*args[-2:])

        return t.cast("WSGIApplication", application)

    def _get_file_stream(
        self,
        total_content_length: t.Optional[int],
        content_type: t.Optional[str],
        filename: t.Optional[str] = None,
        content_length: t.Optional[int] = None,
    ) -> t.IO[bytes]:
        """Called to get a stream for the file upload.

        :param total_content_length: the total content length of all the
                                     data in the request combined.  This value
                                     is guaranteed to be there.
        :param content_type: the mimetype of the uploaded file.
        :param filename: the filename of the uploaded file.  May be `None`.
        :param content_length: the length of this file.  This value is usually
                               not provided because webbrowsers do not provide
                               this value.
        """
        return default_stream_factory(
            total_content_length=total_content_length,
            filename=filename,
            content_type=content_type,
            content_length=content_length,
        )

    @property
    def want_form_data_parsed(self) -> bool:
        """``True`` if the request method carries content. By default
        this is true if a ``Content-Type`` is sent.
        """
        return bool(self.environ.get("CONTENT_TYPE"))

    def make_form_data_parser(self) -> FormDataParser:
        """Creates the form data parser."""
        return self.form_data_parser_class(
            self._get_file_stream,
            self.charset,
            self.encoding_errors,
            self.max_form_memory_size,
            self.max_content_length,
            self.parameter_storage_class,
        )

    def _load_form_data(self) -> None:
        """Method used internally to retrieve submitted data."""
        if "form" in self.__dict__:
            return

        if self.want_form_data_parsed:
            parser = self.make_form_data_parser()
            data = parser.parse(
                self._get_stream_for_parsing(),
                self.mimetype,
                self.content_length,
                self.mimetype_params,
            )
        else:
            data = (
                self.stream,
                self.parameter_storage_class(),
                self.parameter_storage_class(),
            )

        d = self.__dict__
        d["stream"], d["form"], d["files"] = data

    def _get_stream_for_parsing(self) -> t.IO[bytes]:
        """This is the same as accessing :attr:`stream` with the difference
        that if it finds cached data from calling :meth:`get_data` first it
        will create a new stream out of the cached data.
        """
        cached_data = getattr(self, "_cached_data", None)
        if cached_data is not None:
            return BytesIO(cached_data)
        return self.stream

    def close(self) -> None:
        """Closes associated resources of this request object.  This
        closes all file handles explicitly.  You can also use the request
        object in a with statement which will automatically close it.
        """
        files = self.__dict__.get("files")
        for _key, value in iter_multi_items(files or ()):
            value.close()

    def __enter__(self) -> "Request":
        return self

    def __exit__(self, exc_type, exc_value, tb) -> None:  # type: ignore
        self.close()

    @cached_property
    def stream(self) -> t.IO[bytes]:
        """
        If the incoming form data was not encoded with a known mimetype
        the data is stored unmodified in this stream for consumption.
        """
        if self.shallow:
            raise RuntimeError(
                "This request was created with 'shallow=True', reading"
                " from the input stream is disabled."
            )

        return get_input_stream(self.environ)

    @cached_property
    def data(self) -> bytes:
        """
        Contains the incoming request data as string in case it came with
        a mimetype that is not handled.
        """
        return self.get_data(parse_form_data=True)

    @typing.overload
    def get_data(  # type: ignore
        self,
        cache: bool = True,
        as_text: "t.Literal[False]" = False,
        parse_form_data: bool = False,
    ) -> bytes:
        ...

    @typing.overload
    def get_data(
        self,
        cache: bool = True,
        as_text: "t.Literal[True]" = ...,
        parse_form_data: bool = False,
    ) -> str:
        ...

    def get_data(
        self, cache: bool = True, as_text: bool = False, parse_form_data: bool = False
    ) -> t.Union[bytes, str]:
        """This reads the buffered incoming data from the client into one
        bytes object."""
        rv = getattr(self, "_cached_data", None)
        if rv is None:
            if parse_form_data:
                self._load_form_data()
            rv = self.stream.read()
            if cache:
                self._cached_data = rv
        if as_text:
            rv = rv.decode(self.charset, self.encoding_errors)
        return rv

    @cached_property
    def form(self) -> "ImmutableMultiDict[str, str]":
        """The form parameters."""
        self._load_form_data()
        return self.form

    @cached_property
    def values(self) -> "CombinedMultiDict[str, str]":
        sources = [self.args]

        if self.method != "GET":
            sources.append(self.form)

        args = []

        for d in sources:
            if not isinstance(d, MultiDict):
                d = MultiDict(d)

            args.append(d)

        return CombinedMultiDict(args)

    @cached_property
    def files(self) -> "ImmutableMultiDict[str, FileStorage]":
        self._load_form_data()
        return self.files
