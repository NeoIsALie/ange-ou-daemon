import json
import sys
import typing
import typing as t
import warnings
from http import HTTPStatus
from itertools import chain

from src.server.datastructures.ClosingIterator import ClosingIterator
from src.server.urls.urls import _to_bytes
from src.server.datastructures.Header import Headers
from src.server.requests.base_response import Response as BaseResponse
from src.server.urls.urls import iri_to_uri
from src.server.utils import cached_property
from src.server.http import http_date

_default_encoding = sys.getdefaultencoding()


def _get_environ(obj: t.Union["WSGIEnvironment", "Request"]) -> "WSGIEnvironment":
    env = getattr(obj, "environ", obj)
    assert isinstance(
        env, dict
    ), f"{type(obj).__name__!r} is not a WSGI environment (has to be a dict)"
    return env


def _iter_encoded(
        iterable: t.Iterable[t.Union[str, bytes]], charset: str
) -> t.Iterator[bytes]:
    for item in iterable:
        if isinstance(item, str):
            yield item.encode(charset)
        else:
            yield item


def _clean_accept_ranges(accept_ranges: t.Union[bool, str]) -> str:
    if accept_ranges is True:
        return "bytes"
    elif accept_ranges is False:
        return "none"
    elif isinstance(accept_ranges, str):
        return accept_ranges
    raise ValueError("Invalid accept_ranges value")


def run_wsgi_app(
        app: "WSGIApplication", environ: "WSGIEnvironment", buffered: bool = False
) -> t.Tuple[t.Iterable[bytes], str, Headers]:
    """Return a tuple in the form (app_iter, status, headers) of the
    application output.  This works best if you pass it an application that
    returns an iterator all the time.
    Sometimes applications may use the `write()` callable returned
    by the `start_response` function.  This tries to resolve such edge
    cases automatically.  But if you don't get the expected output you
    should set `buffered` to `True` which enforces buffering.
    If passed an invalid WSGI application the behavior of this function is
    undefined.  Never pass non-conforming WSGI applications to this function.
    :param app: the application to execute.
    :param buffered: set to `True` to enforce buffering.
    :return: tuple in the form ``(app_iter, status, headers)``
    """
    environ = _get_environ(environ).copy()
    status: str
    response: t.Optional[t.Tuple[str, t.List[t.Tuple[str, str]]]] = None
    buffer: t.List[bytes] = []

    def start_response(status, headers, exc_info=None):  
        nonlocal response

        if exc_info:
            try:
                raise exc_info[1].with_traceback(exc_info[2])
            finally:
                exc_info = None

        response = (status, headers)
        return buffer.append

    app_rv = app(environ, start_response)
    close_func = getattr(app_rv, "close", None)
    app_iter: t.Iterable[bytes] = iter(app_rv)

    if buffered:
        try:
            app_iter = list(app_iter)
        finally:
            if close_func is not None:
                close_func()

    else:
        for item in app_iter:
            buffer.append(item)

            if response is not None:
                break

        if buffer:
            app_iter = chain(buffer, app_iter)

        if close_func is not None and app_iter is not app_rv:
            app_iter = ClosingIterator(app_iter, close_func)

    status, headers = response  
    return app_iter, status, Headers(headers)


class Response(BaseResponse):
    """Represents an outgoing WSGI HTTP response with body, status, and
    headers. Has properties and methods for using the functionality
    defined by various HTTP specs.

    :param response: The data for the body of the response. A string or
        bytes, or tuple or list of strings or bytes, for a fixed-length
        response, or any other iterable of strings or bytes for a
        streaming response. Defaults to an empty body.
    :param status: The status code for the response. Either an int, in
        which case the default status message is added, or a string in
        the form ``{code} {message}``, like ``404 Not Found``. Defaults
        to 200.
    :param headers: A :class:`~datastructures.Headers` object,
        or a list of ``(key, value)`` tuples that will be converted to a
        ``Headers`` object.
    :param mimetype: The mime type (content type without charset or
        other parameters) of the response. If the value starts with
        ``text/`` (or matches some other special cases), the charset
        will be added to create the ``content_type``.
    :param content_type: The full content type of the response.
        Overrides building the value from ``mimetype``.
    :param direct_passthrough: Pass the response body directly through
        as the WSGI iterable. This can be used when the body is a binary
        file or other iterator of bytes, to skip some unnecessary
        checks. Use :func:`~utils.send_file` instead of setting
        this manually.
    """

    implicit_sequence_conversion = True
    autocorrect_location_header = True
    automatically_set_content_length = True
    response: t.Union[t.Iterable[str], t.Iterable[bytes]]

    def __init__(
            self,
            response: t.Optional[
                t.Union[t.Iterable[bytes], bytes, t.Iterable[str], str]
            ] = None,
            status: t.Optional[t.Union[int, str, HTTPStatus]] = None,
            headers: t.Optional[
                t.Union[
                    t.Mapping[str, t.Union[str, int, t.Iterable[t.Union[str, int]]]],
                    t.Iterable[t.Tuple[str, t.Union[str, int]]],
                ]
            ] = None,
            mimetype: t.Optional[str] = None,
            content_type: t.Optional[str] = None,
            direct_passthrough: bool = False,
    ) -> None:
        super().__init__(
            status=status,
            headers=headers,
            mimetype=mimetype,
            content_type=content_type,
        )

        self.direct_passthrough = direct_passthrough
        self._on_close: t.List[t.Callable[[], t.Any]] = []
        if response is None:
            self.response = []
        elif isinstance(response, (str, bytes, bytearray)):
            self.set_data(response)
        else:
            self.response = response

    def call_on_close(self, func: t.Callable[[], t.Any]) -> t.Callable[[], t.Any]:
        """Adds a function to the internal list of functions that should
        be called as part of closing down the response."""
        self._on_close.append(func)
        return func

    def __repr__(self) -> str:
        if self.is_sequence:
            body_info = f"{sum(map(len, self.iter_encoded()))} bytes"
        else:
            body_info = "streamed" if self.is_streamed else "likely-streamed"
        return f"<{type(self).__name__} {body_info} [{self.status}]>"

    @classmethod
    def force_type(
            cls, response: "Response", environ: t.Optional["WSGIEnvironment"] = None
    ) -> "Response":
        """Enforce that the WSGI response is a response object of the current
        type.

        :param response: a response object or wsgi application.
        :param environ: a WSGI environment object.
        :return: a response object.
        """
        if not isinstance(response, Response):
            if environ is None:
                raise TypeError(
                    "cannot convert WSGI application into response"
                    " objects without an environ"
                )
            response = Response(*run_wsgi_app(response, environ))

        response.__class__ = cls
        return response

    @classmethod
    def from_app(
            cls, app: "WSGIApplication", environ: "WSGIEnvironment", buffered: bool = False
    ) -> "Response":
        """Create a new response object from an application output.

        :param app: the WSGI application to execute.
        :param environ: the WSGI environment to execute against.
        :param buffered: set to `True` to enforce buffering.
        :return: a response object.
        """

        return cls(*run_wsgi_app(app, environ, buffered))

    @typing.overload
    def get_data(self, as_text: "te.Literal[False]" = False) -> bytes:
        ...

    @typing.overload
    def get_data(self, as_text: "te.Literal[True]") -> str:
        ...

    def get_data(self, as_text: bool = False) -> t.Union[bytes, str]:
        """The string representation of the response body."""
        self._ensure_sequence()
        rv = b"".join(self.iter_encoded())

        if as_text:
            return rv.decode(self.charset)

        return rv

    def set_data(self, value: t.Union[bytes, str]) -> None:
        """Sets a new string as response."""
        if isinstance(value, str):
            value = value.encode(self.charset)
        else:
            value = bytes(value)
        self.response = [value]
        if self.automatically_set_content_length:
            self.headers["Content-Length"] = str(len(value))

    data = property(
        get_data,
        set_data,
        doc="A descriptor that calls :meth:`get_data` and :meth:`set_data`.",
    )

    def calculate_content_length(self) -> t.Optional[int]:
        """Returns the content length if available or `None` otherwise."""
        try:
            self._ensure_sequence()
        except RuntimeError:
            return None
        return sum(len(x) for x in self.iter_encoded())

    def _ensure_sequence(self, mutable: bool = False) -> None:
        if self.is_sequence:
            # if we need a mutable object, we ensure it's a list.
            if mutable and not isinstance(self.response, list):
                self.response = list(self.response)  
            return
        if self.direct_passthrough:
            raise RuntimeError(
                "Attempted implicit sequence conversion but the"
                " response object is in direct passthrough mode."
            )
        if not self.implicit_sequence_conversion:
            raise RuntimeError(
                "The response object required the iterable to be a"
                " sequence, but the implicit conversion was disabled."
                " Call make_sequence() yourself."
            )
        self.make_sequence()

    def make_sequence(self) -> None:
        if not self.is_sequence:
            # if we consume an iterable we have to ensure that the close
            # method of the iterable is called if available when we tear
            # down the response
            close = getattr(self.response, "close", None)
            self.response = list(self.iter_encoded())
            if close is not None:
                self.call_on_close(close)

    def iter_encoded(self) -> t.Iterator[bytes]:
        return _iter_encoded(self.response, self.charset)

    @property
    def is_streamed(self) -> bool:
        try:
            len(self.response)  
        except (TypeError, AttributeError):
            return True
        return False

    @property
    def is_sequence(self) -> bool:
        return isinstance(self.response, (tuple, list))

    def close(self) -> None:
        """Close the wrapped response if possible.  You can also use the object
        in a with statement which will automatically close it.
        """
        if hasattr(self.response, "close"):
            self.response.close()  
        for func in self._on_close:
            func()

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, exc_type, exc_value, tb):  
        self.close()

    def get_wsgi_headers(self, environ: "WSGIEnvironment") -> Headers:
        """This is automatically called right before the response is started
        and returns headers modified for the given environment.


        :param environ: the WSGI environment of the request.
        :return: returns a new :class:`datastructures.Headers`
                 object.
        """
        headers = Headers(self.headers)
        location: t.Optional[str] = None
        content_location: t.Optional[str] = None
        content_length: t.Optional[t.Union[str, int]] = None
        status = self.status_code

        for key, value in headers:
            ikey = key.lower()
            if ikey == "location":
                location = value
            elif ikey == "content-location":
                content_location = value
            elif ikey == "content-length":
                content_length = value

        if location is not None:
            old_location = location
            if isinstance(location, str):
                location = iri_to_uri(location, safe_conversion=True)

            if location != old_location:
                headers["Location"] = location
        if content_location is not None and isinstance(content_location, str):
            headers["Content-Location"] = iri_to_uri(content_location)

        if 100 <= status < 200 or status == 204:
            headers.remove("Content-Length")
        if (
                self.automatically_set_content_length
                and self.is_sequence
                and content_length is None
                and status not in (204, 304)
                and not (100 <= status < 200)
        ):
            try:
                content_length = sum(len(_to_bytes(x, "ascii")) for x in self.response)
            except UnicodeError:
                # Something other than bytes, can't safely figure out
                # the length of the response.
                pass
            else:
                headers["Content-Length"] = str(content_length)

        return headers

    def get_app_iter(self, environ: "WSGIEnvironment") -> t.Iterable[bytes]:
        """Returns the application iterator for the given environ. """
        status = self.status_code
        if (
                environ["REQUEST_METHOD"] == "HEAD"
                or 100 <= status < 200
                or status in (204, 304)
        ):
            iterable: t.Iterable[bytes] = ()
        elif self.direct_passthrough:
            return self.response  
        else:
            iterable = self.iter_encoded()
        return ClosingIterator(iterable, self.close)

    def get_wsgi_response(
            self, environ: "WSGIEnvironment"
    ) -> t.Tuple[t.Iterable[bytes], str, t.List[t.Tuple[str, str]]]:
        """Returns the final WSGI response as tuple.

        :param environ: the WSGI environment of the request.
        :return: an ``(app_iter, status, headers)`` tuple.
        """
        headers = self.get_wsgi_headers(environ)
        app_iter = self.get_app_iter(environ)
        return app_iter, self.status, headers.to_wsgi_list()

    def __call__(
            self, environ: "WSGIEnvironment", start_response: "StartResponse"
    ) -> t.Iterable[bytes]:
        """Process this response as WSGI application.

        :param environ: the WSGI environment.
        :param start_response: the response callable provided by the WSGI
                               server.
        :return: an application iterator
        """
        app_iter, status, headers = self.get_wsgi_response(environ)
        start_response(status, headers)
        return app_iter
