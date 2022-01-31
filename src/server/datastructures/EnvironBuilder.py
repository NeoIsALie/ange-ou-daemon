import sys
import typing as t
from collections import defaultdict
from io import BytesIO

from src.server.datastructures import MultiDict, Headers, EnvironHeaders, CallbackDict, CombinedMultiDict
from src.server.datastructures.FileMultiDict import FileMultiDict
from src.server.datastructures.Header import dump_options_header
from src.server.http import parse_options_header
from src.server.requests.base_response import get_content_type
from src.server.requests.request_extended import Request
from src.server.urls.urls import _make_encode_wrapper, url_parse, iri_to_uri, url_fix, url_unparse, url_encode, \
    url_unquote
from src.server.utils import _wsgi_decoding_dance, _wsgi_encoding_dance

_TAnyMultiDict = t.TypeVar("_TAnyMultiDict", bound=MultiDict)


class EnvironBuilder:
    """This class can be used to conveniently create a WSGI environment.

    :param path: the path of the request.  In the WSGI environment this will
                 end up as `PATH_INFO`.  If the `query_string` is not defined
                 and there is a question mark in the `path` everything after
                 it is used as query string.
    :param base_url: the base URL is a URL that is used to extract the WSGI
                     URL scheme, host (server name + server port) and the
                     script root (`SCRIPT_NAME`).
    :param query_string: an optional string or dict with URL parameters.
    :param method: the HTTP method to use, defaults to `GET`.
    :param input_stream: an optional input stream.  Do not specify this and
                         `data`.  As soon as an input stream is set you can't
                         modify :attr:`args` and :attr:`files` unless you
                         set the :attr:`input_stream` to `None` again.
    :param content_type: The content type for the request.  As of 0.5 you
                         don't have to provide this when specifying files
                         and form data via `data`.
    :param content_length: The content length for the request.  You don't
                           have to specify this when providing data via
                           `data`.
    :param errors_stream: an optional error stream that is used for
                          `wsgi.errors`.  Defaults to :data:`stderr`.
    :param multithread: controls `wsgi.multithread`.  Defaults to `False`.
    :param multiprocess: controls `wsgi.multiprocess`.  Defaults to `False`.
    :param run_once: controls `wsgi.run_once`.  Defaults to `False`.
    :param headers: an optional list or :class:`Headers` object of headers.
    :param data: a string or dict of form data or a file-object.
                 See explanation above.
    :param json: An object to be serialized and assigned to ``data``.
        Defaults the content type to ``"application/json"``.
        Serialized with the function assigned to :attr:`json_dumps`.
    :param environ_base: an optional dict of environment defaults.
    :param environ_overrides: an optional dict of environment overrides.
    :param charset: the charset used to encode string data.
    """

    #: the server protocol to use.  defaults to HTTP/1.1
    server_protocol = "HTTP/1.1"

    #: the wsgi version to use.  defaults to (1, 0)
    wsgi_version = (1, 0)

    #: The default request class used by :meth:`get_request`.
    request_class = Request

    import json

    #: The serialization function used when ``json`` is passed.
    json_dumps = staticmethod(json.dumps)
    del json

    _args: t.Optional[MultiDict]
    _query_string: t.Optional[str]
    _input_stream: t.Optional[t.IO[bytes]]
    _form: t.Optional[MultiDict]
    _files: t.Optional[FileMultiDict]

    def __init__(
        self,
        path: str = "/",
        base_url: t.Optional[str] = None,
        query_string: t.Optional[t.Union[t.Mapping[str, str], str]] = None,
        method: str = "GET",
        input_stream: t.Optional[t.IO[bytes]] = None,
        content_type: t.Optional[str] = None,
        content_length: t.Optional[int] = None,
        errors_stream: t.Optional[t.IO[str]] = None,
        multithread: bool = False,
        multiprocess: bool = False,
        run_once: bool = False,
        headers: t.Optional[t.Union[Headers, t.Iterable[t.Tuple[str, str]]]] = None,
        data: t.Optional[
            t.Union[t.IO[bytes], str, bytes, t.Mapping[str, t.Any]]
        ] = None,
        environ_base: t.Optional[t.Mapping[str, t.Any]] = None,
        environ_overrides: t.Optional[t.Mapping[str, t.Any]] = None,
        charset: str = "utf-8",
        mimetype: t.Optional[str] = None,
        json: t.Optional[t.Mapping[str, t.Any]] = None,
    ) -> None:
        path_s = _make_encode_wrapper(path)
        if query_string is not None and path_s("?") in path:
            raise ValueError("Query string is defined in the path and as an argument")
        request_uri = url_parse(path)
        if query_string is None and path_s("?") in path:
            query_string = request_uri.query
        self.charset = charset
        self.path = iri_to_uri(request_uri.path)
        self.request_uri = path
        if base_url is not None:
            base_url = url_fix(iri_to_uri(base_url, charset), charset)
        self.base_url = base_url  
        if isinstance(query_string, (bytes, str)):
            self.query_string = query_string
        else:
            if query_string is None:
                query_string = MultiDict()
            elif not isinstance(query_string, MultiDict):
                query_string = MultiDict(query_string)
            self.args = query_string
        self.method = method
        if headers is None:
            headers = Headers()
        elif not isinstance(headers, Headers):
            headers = Headers(headers)
        self.headers = headers
        if content_type is not None:
            self.content_type = content_type
        if errors_stream is None:
            errors_stream = sys.stderr
        self.errors_stream = errors_stream
        self.multithread = multithread
        self.multiprocess = multiprocess
        self.run_once = run_once
        self.environ_base = environ_base
        self.environ_overrides = environ_overrides
        self.input_stream = input_stream
        self.content_length = content_length
        self.closed = False

        if json is not None:
            if data is not None:
                raise TypeError("can't provide both json and data")

            data = self.json_dumps(json)

            if self.content_type is None:
                self.content_type = "application/json"

        if data:
            if input_stream is not None:
                raise TypeError("can't provide input stream and data")
            if hasattr(data, "read"):
                data = data.read()  
            if isinstance(data, str):
                data = data.encode(self.charset)
            if isinstance(data, bytes):
                self.input_stream = BytesIO(data)
                if self.content_length is None:
                    self.content_length = len(data)
            else:
                for key, value in _iter_data(data):  
                    if isinstance(value, (tuple, dict)) or hasattr(value, "read"):
                        self._add_file_from_data(key, value)
                    else:
                        self.form.setlistdefault(key).append(value)

        if mimetype is not None:
            self.mimetype = mimetype

    @classmethod
    def from_environ(
        cls, environ: "WSGIEnvironment", **kwargs: t.Any
    ) -> "EnvironBuilder":
        """Turn an environ dict back into a builder. Any extra kwargs
        override the args extracted from the environ.
        """
        headers = Headers(EnvironHeaders(environ))
        out = {
            "path": _wsgi_decoding_dance(environ["PATH_INFO"]),
            "base_url": cls._make_base_url(
                environ["wsgi.url_scheme"],
                headers.pop("Host"),
                _wsgi_decoding_dance(environ["SCRIPT_NAME"]),
            ),
            "query_string": _wsgi_decoding_dance(environ["QUERY_STRING"]),
            "method": environ["REQUEST_METHOD"],
            "input_stream": environ["wsgi.input"],
            "content_type": headers.pop("Content-Type", None),
            "content_length": headers.pop("Content-Length", None),
            "errors_stream": environ["wsgi.errors"],
            "multithread": environ["wsgi.multithread"],
            "multiprocess": environ["wsgi.multiprocess"],
            "run_once": environ["wsgi.run_once"],
            "headers": headers,
        }
        out.update(kwargs)
        return cls(**out)

    def _add_file_from_data(
        self,
        key: str,
        value: t.Union[
            t.IO[bytes], t.Tuple[t.IO[bytes], str], t.Tuple[t.IO[bytes], str, str]
        ],
    ) -> None:
        """Called in the EnvironBuilder to add files from the data dict."""
        if isinstance(value, tuple):
            self.files.add_file(key, *value)
        else:
            self.files.add_file(key, value)

    @staticmethod
    def _make_base_url(scheme: str, host: str, script_root: str) -> str:
        return url_unparse((scheme, host, script_root, "", "")).rstrip("/") + "/"

    @property
    def base_url(self) -> str:
        """The base URL is used to extract the URL scheme, host name,
        port, and root path.
        """
        return self._make_base_url(self.url_scheme, self.host, self.script_root)

    @base_url.setter
    def base_url(self, value: t.Optional[str]) -> None:
        if value is None:
            scheme = "http"
            netloc = "localhost"
            script_root = ""
        else:
            scheme, netloc, script_root, qs, anchor = url_parse(value)
            if qs or anchor:
                raise ValueError("base url must not contain a query string or fragment")
        self.script_root = script_root.rstrip("/")
        self.host = netloc
        self.url_scheme = scheme

    @property
    def content_type(self) -> t.Optional[str]:
        """The content type for the request.  Reflected from and to
        the :attr:`headers`.  Do not set if you set :attr:`files` or
        :attr:`form` for auto detection.
        """
        ct = self.headers.get("Content-Type")
        if ct is None and not self._input_stream:
            if self._files:
                return "multipart/form-data"
            if self._form:
                return "application/x-www-form-urlencoded"
            return None
        return ct

    @content_type.setter
    def content_type(self, value: t.Optional[str]) -> None:
        if value is None:
            self.headers.pop("Content-Type", None)
        else:
            self.headers["Content-Type"] = value

    @property
    def mimetype(self) -> t.Optional[str]:
        """The mimetype (content type without charset etc.)
        """
        ct = self.content_type
        return ct.split(";")[0].strip() if ct else None

    @mimetype.setter
    def mimetype(self, value: str) -> None:
        self.content_type = get_content_type(value, self.charset)

    @property
    def content_length(self) -> t.Optional[int]:
        """The content length as integer.
        """
        return self.headers.get("Content-Length", type=int)

    @content_length.setter
    def content_length(self, value: t.Optional[int]) -> None:
        if value is None:
            self.headers.pop("Content-Length", None)
        else:
            self.headers["Content-Length"] = str(value)

    def _get_form(self, name: str, storage: t.Type[_TAnyMultiDict]) -> _TAnyMultiDict:
        """Common behavior for getting the :attr:`form` and
        :attr:`files` properties.

        :param name: Name of the internal cached attribute.
        :param storage: Storage class used for the data.
        """
        if self.input_stream is not None:
            raise AttributeError("an input stream is defined")

        rv = getattr(self, name)

        if rv is None:
            rv = storage()
            setattr(self, name, rv)

        return rv  

    def _set_form(self, name: str, value: MultiDict) -> None:
        """Common behavior for setting the :attr:`form` and
        :attr:`files` properties.

        :param name: Name of the internal cached attribute.
        :param value: Value to assign to the attribute.
        """
        self._input_stream = None
        setattr(self, name, value)

    @property
    def form(self) -> MultiDict:
        """A :class:`MultiDict` of form values."""
        return self._get_form("_form", MultiDict)

    @form.setter
    def form(self, value: MultiDict) -> None:
        self._set_form("_form", value)

    @property
    def files(self) -> FileMultiDict:
        """A :class:`FileMultiDict` of uploaded files. Use
        :meth:`~FileMultiDict.add_file` to add new files.
        """
        return self._get_form("_files", FileMultiDict)

    @files.setter
    def files(self, value: FileMultiDict) -> None:
        self._set_form("_files", value)

    @property
    def input_stream(self) -> t.Optional[t.IO[bytes]]:
        """An optional input stream."""
        return self._input_stream

    @input_stream.setter
    def input_stream(self, value: t.Optional[t.IO[bytes]]) -> None:
        self._input_stream = value
        self._form = None
        self._files = None

    @property
    def query_string(self) -> str:
        """The query string.  If you set this to a string
        :attr:`args` will no longer be available.
        """
        if self._query_string is None:
            if self._args is not None:
                return url_encode(self._args, charset=self.charset)
            return ""
        return self._query_string

    @query_string.setter
    def query_string(self, value: t.Optional[str]) -> None:
        self._query_string = value
        self._args = None

    @property
    def args(self) -> MultiDict:
        """The URL arguments as :class:`MultiDict`."""
        if self._query_string is not None:
            raise AttributeError("a query string is defined")
        if self._args is None:
            self._args = MultiDict()
        return self._args

    @args.setter
    def args(self, value: t.Optional[MultiDict]) -> None:
        self._query_string = None
        self._args = value

    @property
    def server_name(self) -> str:
        """The server name (read-only, use :attr:`host` to set)"""
        return self.host.split(":", 1)[0]

    @property
    def server_port(self) -> int:
        """The server port as integer (read-only, use :attr:`host` to set)"""
        pieces = self.host.split(":", 1)
        if len(pieces) == 2 and pieces[1].isdigit():
            return int(pieces[1])
        if self.url_scheme == "https":
            return 443
        return 80

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        """Closes all files."""
        if self.closed:
            return
        try:
            files = self.files.values()
        except AttributeError:
            files = ()  
        for f in files:
            try:
                f.close()
            except Exception:
                pass
        self.closed = True

    def get_environ(self):
        input_stream = self.input_stream
        content_length = self.content_length

        mimetype = self.mimetype
        content_type = self.content_type

        if input_stream is not None:
            start_pos = input_stream.tell()
            input_stream.seek(0, 2)
            end_pos = input_stream.tell()
            input_stream.seek(start_pos)
            content_length = end_pos - start_pos
        elif mimetype == "multipart/form-data":
            input_stream, content_length, boundary = stream_encode_multipart(
                CombinedMultiDict([self.form, self.files]), charset=self.charset
            )
            content_type = f'{mimetype}; boundary="{boundary}"'
        elif mimetype == "application/x-www-form-urlencoded":
            form_encoded = url_encode(self.form, charset=self.charset).encode("ascii")
            content_length = len(form_encoded)
            input_stream = BytesIO(form_encoded)
        else:
            input_stream = BytesIO()

        result: "WSGIEnvironment" = {}
        if self.environ_base:
            result.update(self.environ_base)

        def _path_encode(x: str) -> str:
            return _wsgi_encoding_dance(url_unquote(x, self.charset), self.charset)

        raw_uri = _wsgi_encoding_dance(self.request_uri, self.charset)
        result.update(
            {
                "REQUEST_METHOD": self.method,
                "SCRIPT_NAME": _path_encode(self.script_root),
                "PATH_INFO": _path_encode(self.path),
                "QUERY_STRING": _wsgi_encoding_dance(self.query_string, self.charset),
                # Non-standard, added by mod_wsgi, uWSGI
                "REQUEST_URI": raw_uri,
                # Non-standard, added by gunicorn
                "RAW_URI": raw_uri,
                "SERVER_NAME": self.server_name,
                "SERVER_PORT": str(self.server_port),
                "HTTP_HOST": self.host,
                "SERVER_PROTOCOL": self.server_protocol,
                "wsgi.version": self.wsgi_version,
                "wsgi.url_scheme": self.url_scheme,
                "wsgi.input": input_stream,
                "wsgi.errors": self.errors_stream,
                "wsgi.multithread": self.multithread,
                "wsgi.multiprocess": self.multiprocess,
                "wsgi.run_once": self.run_once,
            }
        )

        headers = self.headers.copy()

        if content_type is not None:
            result["CONTENT_TYPE"] = content_type
            headers.set("Content-Type", content_type)

        if content_length is not None:
            result["CONTENT_LENGTH"] = str(content_length)
            headers.set("Content-Length", content_length)

        combined_headers = defaultdict(list)

        for key, value in headers.to_wsgi_list():
            combined_headers[f"HTTP_{key.upper().replace('-', '_')}"].append(value)

        for key, values in combined_headers.items():
            result[key] = ", ".join(values)

        if self.environ_overrides:
            result.update(self.environ_overrides)

        return result

    def get_request(self, cls: t.Optional[t.Type[Request]] = None) -> Request:
        """Returns a request with the data.  If the request class is not
        specified :attr:`request_class` is used.

        :param cls: The request wrapper to use.
        """
        if cls is None:
            cls = self.request_class

        return cls(self.get_environ())