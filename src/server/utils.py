import io
import mimetypes
import os
import typing as t
import unicodedata
from datetime import datetime
from io import BytesIO

from src.server.datastructures import LimitedStream
from src.server.datastructures.Header import Headers, _missing
from src.server.file_ops import wrap_file
from src.server.urls import url_quote

_T = t.TypeVar("_T")

_TAccessorValue = t.TypeVar("_TAccessorValue")


def _get_server(
        environ,
) -> t.Optional[t.Tuple[str, t.Optional[int]]]:
    name = environ.get("SERVER_NAME")

    if name is None:
        return None

    try:
        port: t.Optional[int] = int(environ.get("SERVER_PORT", None))
    except (TypeError, ValueError):
        # unix socket
        port = None

    return name, port


def get_content_length(environ) -> t.Optional[int]:
    """Returns the content length from the WSGI environment as
    integer. If it's not available or chunked transfer encoding is used,
    ``None`` is returned.
    :param environ: the WSGI environ to fetch the content length from.
    """
    if environ.get("HTTP_TRANSFER_ENCODING", "") == "chunked":
        return None

    content_length = environ.get("CONTENT_LENGTH")
    if content_length is not None:
        try:
            return max(0, int(content_length))
        except (ValueError, TypeError):
            pass
    return None


def get_input_stream(
        environ, safe_fallback: bool = True
) -> t.IO[bytes]:
    """Returns the input stream.
    :param safe_fallback: use an empty stream as a safe fallback when the
        content length is not set.
    """
    stream = t.cast(t.IO[bytes], environ["wsgi.input"])
    content_length = get_content_length(environ)

    if environ.get("wsgi.input_terminated"):
        return stream

    if content_length is None:
        return BytesIO() if safe_fallback else stream
    return t.cast(t.IO[bytes], LimitedStream(stream, content_length))


def _wsgi_decoding_dance(
        s: str, charset: str = "utf-8", errors: str = "replace"
) -> str:
    return s.encode("latin1").decode(charset, errors)


def _wsgi_encoding_dance(
        s: str, charset: str = "utf-8", errors: str = "replace"
) -> str:
    if isinstance(s, bytes):
        return s.decode("latin1", errors)

    return s.encode(charset).decode("latin1", errors)


def _get_environ(obj: t.Union["WSGIEnvironment", "Request"]) -> "WSGIEnvironment":
    env = getattr(obj, "environ", obj)
    assert isinstance(
        env, dict
    ), f"{type(obj).__name__!r} is not a WSGI environment (has to be a dict)"
    return


class cached_property(property, t.Generic[_T]):
    """A :func:`property` that is only evaluated once. Subsequent access
    returns the cached value. Setting the property sets the cached
    value. Deleting the property clears the cached value, accessing it
    again will evaluate it again.
    """

    def __init__(
            self,
            fget: t.Callable[[t.Any], _T],
            name: t.Optional[str] = None,
            doc: t.Optional[str] = None,
    ) -> None:
        super().__init__(fget, doc=doc)
        self.__name__ = name or fget.__name__
        self.__module__ = fget.__module__

    def __set__(self, obj: object, value: _T) -> None:
        obj.__dict__[self.__name__] = value

    def __get__(self, obj: object, type: type = None) -> _T:  
        if obj is None:
            return self

        value: _T = obj.__dict__.get(self.__name__, _missing)

        if value is _missing:
            value = self.fget(obj)  
            obj.__dict__[self.__name__] = value

        return value

    def __delete__(self, obj: object) -> None:
        del obj.__dict__[self.__name__]


def send_file(
    path_or_file: t.Union[os.PathLike, str, t.IO[bytes]],
    environ: "WSGIEnvironment",
    mimetype: t.Optional[str] = None,
    as_attachment: bool = False,
    download_name: t.Optional[str] = None,
    last_modified: t.Optional[t.Union[datetime, int, float]] = None,
    use_x_sendfile: bool = False,
    response_class: t.Optional[t.Type["Response"]] = None,
    _root_path: t.Optional[t.Union[os.PathLike, str]] = None,
) -> "Response":
    """Send the contents of a file to the client.
    :param path_or_file: The path to the file to send, relative to the
        current working directory if a relative path is given.
        Alternatively, a file-like object opened in binary mode. Make
        sure the file pointer is seeked to the start of the data.
    :param environ: The WSGI environ for the current request.
    :param mimetype: The MIME type to send for the file. If not
        provided, it will try to detect it from the file name.
    :param as_attachment: Indicate to a browser that it should offer to
        save the file instead of displaying it.
    :param download_name: The default name browsers will use when saving
        the file. Defaults to the passed file name.
    :param use_x_sendfile: Set the ``X-Sendfile`` header to let the
        server to efficiently send the file.
    :param response_class: Build the response using this class.
    """
    path: t.Optional[str] = None
    file: t.Optional[t.IO[bytes]] = None
    size: t.Optional[int] = None
    mtime: t.Optional[float] = None
    headers = Headers()

    if isinstance(path_or_file, (os.PathLike, str)) or hasattr(
        path_or_file, "__fspath__"
    ):
        path_or_file = t.cast(t.Union[os.PathLike, str], path_or_file)

        if _root_path is not None:
            path = os.path.join(_root_path, path_or_file)
        else:
            path = os.path.abspath(path_or_file)

        stat = os.stat(path)
        size = stat.st_size
        mtime = stat.st_mtime
    else:
        file = path_or_file

    if download_name is None and path is not None:
        download_name = os.path.basename(path)

    if mimetype is None:
        if download_name is None:
            raise TypeError(
                "Unable to detect the MIME type because a file name is"
                " not available. Either set 'download_name', pass a"
                " path instead of a file, or set 'mimetype'."
            )

        mimetype, encoding = mimetypes.guess_type(download_name)

        if mimetype is None:
            mimetype = "application/octet-stream"

        if encoding is not None and not as_attachment:
            headers.set("Content-Encoding", encoding)

    if download_name is not None:
        try:
            download_name.encode("ascii")
        except UnicodeEncodeError:
            simple = unicodedata.normalize("NFKD", download_name)
            simple = simple.encode("ascii", "ignore").decode("ascii")
            quoted = url_quote(download_name, safe="")
            names = {"filename": simple, "filename*": f"UTF-8''{quoted}"}
        else:
            names = {"filename": download_name}

        value = "attachment" if as_attachment else "inline"
        headers.set("Content-Disposition", value, **names)
    elif as_attachment:
        raise TypeError(
            "No name provided for attachment. Either set"
            " 'download_name' or pass a path instead of a file."
        )

    if use_x_sendfile and path is not None:
        headers["X-Sendfile"] = path
        data = None
    else:
        if file is None:
            file = open(path, "rb")  
        elif isinstance(file, io.BytesIO):
            size = file.getbuffer().nbytes
        elif isinstance(file, io.TextIOBase):
            raise ValueError("Files must be opened in binary mode or use BytesIO.")

        data = wrap_file(environ, file)

    rv = response_class(
        data, mimetype=mimetype, headers=headers, direct_passthrough=True
    )

    if size is not None:
        rv.content_length = size

    if last_modified is not None:
        rv.last_modified = last_modified  
    elif mtime is not None:
        rv.last_modified = mtime
    return rv
