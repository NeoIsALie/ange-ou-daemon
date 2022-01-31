import errno
import io
import logging
import os
import socket
import socketserver
import ssl
import sys
import typing as t
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

from .exceptions import InternalServerError
from src.server.urls.urls import uri_to_iri, url_parse, url_unquote
from .utils import _wsgi_encoding_dance

_log_add_style = True


def is_running_from_reloader() -> bool:
    """Check if the server is running as a subprocess within the reloader
    """
    return os.environ.get("RUN_MAIN") == "true"


def _has_level_handler(logger: logging.Logger) -> bool:
    """Check if there is a handler in the logging chain that will handle
    the given logger's effective level.
    """
    level = logger.getEffectiveLevel()
    current = logger

    while current:
        if any(handler.level <= level for handler in current.handlers):
            return True

        if not current.propagate:
            break

        current = current.parent  

    return False


class _ColorStreamHandler(logging.StreamHandler):
    """On Windows, wrap stream with Colorama for ANSI style support."""

    def __init__(self) -> None:
        try:
            import colorama
        except ImportError:
            stream = None
        else:
            stream = colorama.AnsiToWin32(sys.stderr)

        super().__init__(stream)


_logger: t.Optional[logging.Logger] = None


def _log(type: str, message: str, *args: t.Any, **kwargs: t.Any) -> None:
    global _logger

    if _logger is None:
        _logger = logging.getLogger("app")

        if _logger.level == logging.NOTSET:
            _logger.setLevel(logging.INFO)

        if not _has_level_handler(_logger):
            _logger.addHandler(_ColorStreamHandler())

    getattr(_logger, type)(message.rstrip(), *args, **kwargs)


if os.name == "nt":
    try:
        __import__("colorama")
    except ImportError:
        _log_add_style = False

can_fork = hasattr(os, "fork")

if can_fork:
    ForkingMixIn = socketserver.ForkingMixIn
else:

    class ForkingMixIn:  
        pass

try:
    af_unix = socket.AF_UNIX
except AttributeError:
    af_unix = None  

LISTEN_QUEUE = 128

_TSSLContextArg = t.Optional[
    t.Union["ssl.SSLContext", t.Tuple[str, t.Optional[str]], "te.Literal['adhoc']"]
]


class DechunkedInput(io.RawIOBase):
    """An input stream that handles Transfer-Encoding 'chunked'"""

    def __init__(self, rfile: t.IO[bytes]) -> None:
        self._rfile = rfile
        self._done = False
        self._len = 0

    def readable(self) -> bool:
        return True

    def read_chunk_len(self) -> int:
        try:
            line = self._rfile.readline().decode("latin1")
            _len = int(line.strip(), 16)
        except ValueError as e:
            raise OSError("Invalid chunk header") from e
        if _len < 0:
            raise OSError("Negative chunk length not allowed")
        return _len

    def readinto(self, buf: bytearray) -> int:  
        read = 0
        while not self._done and read < len(buf):
            if self._len == 0:
                self._len = self.read_chunk_len()

            if self._len == 0:
                self._done = True

            if self._len > 0:
                n = min(len(buf), self._len)

                if read + n > len(buf):
                    buf[read:] = self._rfile.read(len(buf) - read)
                    self._len -= len(buf) - read
                    read = len(buf)
                else:
                    buf[read: read + n] = self._rfile.read(n)
                    self._len -= n
                    read += n

            if self._len == 0:
                terminator = self._rfile.readline()
                if terminator not in (b"\n", b"\r\n", b"\r"):
                    raise OSError("Missing chunk terminating newline")

        return read


class WSGIRequestHandler(BaseHTTPRequestHandler):
    """A request handler that implements WSGI dispatching."""

    server: "BaseWSGIServer"

    def make_environ(self) -> "WSGIEnvironment":
        request_url = url_parse(self.path)
        url_scheme = "http"

        if not self.client_address:
            self.client_address = ("<local>", 0)
        elif isinstance(self.client_address, str):
            self.client_address = (self.client_address, 0)

        if not request_url.scheme and request_url.netloc:
            path_info = f"/{request_url.netloc}{request_url.path}"
        else:
            path_info = request_url.path

        path_info = url_unquote(path_info)

        environ: "WSGIEnvironment" = {
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": url_scheme,
            "wsgi.input": self.rfile,
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": self.server.multithread,
            "wsgi.multiprocess": self.server.multiprocess,
            "wsgi.run_once": False,
            "socket": self.connection,
            "SERVER_SOFTWARE": self.server_version,
            "REQUEST_METHOD": self.command,
            "SCRIPT_NAME": "",
            "PATH_INFO": _wsgi_encoding_dance(path_info),
            "QUERY_STRING": _wsgi_encoding_dance(request_url.query),
            "REQUEST_URI": _wsgi_encoding_dance(self.path),
            "RAW_URI": _wsgi_encoding_dance(self.path),
            "REMOTE_ADDR": self.address_string(),
            "REMOTE_PORT": self.port_integer(),
            "SERVER_NAME": self.server.server_address[0],
            "SERVER_PORT": str(self.server.server_address[1]),
            "SERVER_PROTOCOL": self.request_version,
        }

        for key, value in self.headers.items():
            key = key.upper().replace("-", "_")
            value = value.replace("\r\n", "")
            if key not in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                key = f"HTTP_{key}"
                if key in environ:
                    value = f"{environ[key]},{value}"
            environ[key] = value

        if environ.get("HTTP_TRANSFER_ENCODING", "").strip().lower() == "chunked":
            environ["wsgi.input_terminated"] = True
            environ["wsgi.input"] = DechunkedInput(environ["wsgi.input"])

        # Per RFC 2616, if the URL is absolute, use that as the host.
        if request_url.scheme and request_url.netloc:
            environ["HTTP_HOST"] = request_url.netloc

        try:
            peer_cert = self.connection.getpeercert(
                binary_form=True
            )
            if peer_cert is not None:
                environ["SSL_CLIENT_CERT"] = ssl.DER_cert_to_PEM_cert(peer_cert)
        except ValueError:
            self.server.log("error", "Cannot fetch SSL peer certificate info")
        except AttributeError:
            pass

        return environ

    def run_wsgi(self) -> None:
        if self.headers.get("Expect", "").lower().strip() == "100-continue":
            self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")

        self.environ = environ = self.make_environ()
        status_set: t.Optional[str] = None
        headers_set: t.Optional[t.List[t.Tuple[str, str]]] = None
        status_sent: t.Optional[str] = None
        headers_sent: t.Optional[t.List[t.Tuple[str, str]]] = None
        chunk_response: bool = False

        def write(data: bytes) -> None:
            nonlocal status_sent, headers_sent, chunk_response
            assert status_set is not None, "write() before start_response"
            assert headers_set is not None, "write() before start_response"
            if status_sent is None:
                status_sent = status_set
                headers_sent = headers_set
                try:
                    code_str, msg = status_sent.split(None, 1)
                except ValueError:
                    code_str, msg = status_sent, ""
                code = int(code_str)
                self.send_response(code, msg)
                header_keys = set()
                for key, value in headers_sent:
                    self.send_header(key, value)
                    header_keys.add(key.lower())

                if "content-length" not in header_keys:
                    if self.protocol_version >= "HTTP/1.1":
                        chunk_response = True
                        self.send_header("Transfer-Encoding", "chunked")
                    else:
                        self.send_header("Connection", "close")

                self.end_headers()

            assert isinstance(data, bytes), "applications must write bytes"

            if data:
                if chunk_response:
                    self.wfile.write(hex(len(data))[2:].encode())
                    self.wfile.write(b"\r\n")

                self.wfile.write(data)

                if chunk_response:
                    self.wfile.write(b"\r\n")

            self.wfile.flush()

        def start_response(status, headers, exc_info=None):  
            nonlocal status_set, headers_set
            if exc_info:
                try:
                    if headers_sent:
                        raise exc_info[1].with_traceback(exc_info[2])
                finally:
                    exc_info = None
            elif headers_set:
                raise AssertionError("Headers already set")
            status_set = status
            headers_set = headers
            return write

        def execute(app: "WSGIApplication") -> None:
            application_iter = app(environ, start_response)
            try:
                for data in application_iter:
                    write(data)
                if not headers_sent:
                    write(b"")
                if chunk_response:
                    self.wfile.write(b"0\r\n\r\n")
            finally:
                if hasattr(application_iter, "close"):
                    application_iter.close()  

        try:
            execute(self.server.app)
        except (ConnectionError, socket.timeout) as e:
            self.connection_dropped(e, environ)
        except Exception:
            if self.server.passthrough_errors:
                raise

            if status_sent is not None and chunk_response:
                self.close_connection = True

            # traceback = get_current_traceback(ignore_system_exceptions=True)
            try:
                # if we haven't yet sent the headers but they are set
                # we roll back to be able to set them again.
                if status_sent is None:
                    status_set = None
                    headers_set = None
                execute(InternalServerError())
            except Exception:
                pass
            # self.server.log("error", "Error on request:\n%s", traceback.plaintext)

    def handle(self) -> None:
        """Handles a request ignoring dropped connections."""
        try:
            super().handle()
        except (ConnectionError, socket.timeout) as e:
            self.connection_dropped(e)
        except Exception as e:
            raise

    def connection_dropped(
            self, error: BaseException, environ: t.Optional["WSGIEnvironment"] = None
    ) -> None:
        """Called if the connection was closed by the client.  By default
        nothing happens.
        """

    def __getattr__(self, name: str) -> t.Any:
        # All HTTP methods are handled by run_wsgi.
        if name.startswith("do_"):
            return self.run_wsgi

        # All other attributes are forwarded to the base class.
        return getattr(super(), name)

    def address_string(self) -> str:
        if getattr(self, "environ", None):
            return self.environ["REMOTE_ADDR"]  

        if not self.client_address:
            return "<local>"

        return self.client_address[0]

    def port_integer(self) -> int:
        return self.client_address[1]

    def log_request(
            self, code: t.Union[int, str] = "-", size: t.Union[int, str] = "-"
    ) -> None:
        try:
            path = uri_to_iri(self.path)
            msg = f"{self.command} {path} {self.request_version}"
        except AttributeError:
            # path isn't set if the requestline was bad
            msg = self.requestline

        code = str(code)

        if _log_add_style:
            if code[0] == "1":  # 1xx - Informational
                msg = _ansi_style(msg, "bold")
            elif code == "200":  # 2xx - Success
                pass
            elif code == "304":  # 304 - Resource Not Modified
                msg = _ansi_style(msg, "cyan")
            elif code[0] == "3":  # 3xx - Redirection
                msg = _ansi_style(msg, "green")
            elif code == "404":  # 404 - Resource Not Found
                msg = _ansi_style(msg, "yellow")
            elif code[0] == "4":  # 4xx - Client Error
                msg = _ansi_style(msg, "bold", "red")
            else:  # 5xx, or any other response
                msg = _ansi_style(msg, "bold", "magenta")

        self.log("info", '"%s" %s %s', msg, code, size)

    def log_error(self, format: str, *args: t.Any) -> None:
        self.log("error", format, *args)

    def log_message(self, format: str, *args: t.Any) -> None:
        self.log("info", format, *args)

    def log(self, type: str, message: str, *args: t.Any) -> None:
        _log(
            type,
            f"{self.address_string()} - - [{self.log_date_time_string()}] {message}\n",
            *args,
        )


def _ansi_style(value: str, *styles: str) -> str:
    codes = {
        "bold": 1,
        "red": 31,
        "green": 32,
        "yellow": 33,
        "magenta": 35,
        "cyan": 36,
    }

    for style in styles:
        value = f"\x1b[{codes[style]}m{value}"

    return f"{value}\x1b[0m"


def select_address_family(host: str, port: int) -> socket.AddressFamily:
    """Return ``AF_INET4``, ``AF_INET6``, or ``AF_UNIX`` depending on
    the host and port."""
    if host.startswith("unix://"):
        return socket.AF_UNIX
    elif ":" in host and hasattr(socket, "AF_INET6"):
        return socket.AF_INET6
    return socket.AF_INET


def get_sockaddr(
        host: str, port: int, family: socket.AddressFamily
) -> t.Union[t.Tuple[str, int], str]:
    """Return a fully qualified socket address that can be passed to
    :func:`socket.bind`."""
    if family == af_unix:
        return host.split("://", 1)[1]
    try:
        res = socket.getaddrinfo(
            host, port, family, socket.SOCK_STREAM, socket.IPPROTO_TCP
        )
    except socket.gaierror:
        return host, port
    return res[0][4]  


def get_interface_ip(family: socket.AddressFamily) -> str:
    """Get the IP address of an external interface. Used when binding to
    0.0.0.0 or ::1 to show a more useful URL.

    :meta private:
    """
    # arbitrary private address
    host = "fd31:f903:5ab5:1::1" if family == socket.AF_INET6 else "10.253.155.219"

    with socket.socket(family, socket.SOCK_DGRAM) as s:
        try:
            s.connect((host, 58162))
        except OSError:
            return "::1" if family == socket.AF_INET6 else "127.0.0.1"

        return s.getsockname()[0]  


class BaseWSGIServer(HTTPServer):
    """A WSGI server that that handles one request at a time.

    Use :func:`make_server` to create a server instance.
    """

    multithread = False
    multiprocess = False
    request_queue_size = LISTEN_QUEUE

    def __init__(
            self,
            host: str,
            port: int,
            app: "WSGIApplication",
            handler: t.Optional[t.Type[WSGIRequestHandler]] = None,
            passthrough_errors: bool = False,
            fd: t.Optional[int] = None,
    ) -> None:
        if handler is None:
            handler = WSGIRequestHandler
        if "protocol_version" not in vars(handler) and (
                self.multithread or self.multiprocess
        ):
            handler.protocol_version = "HTTP/1.1"

        self.host = host
        self.port = port
        self.app = app
        self.passthrough_errors = passthrough_errors

        self.address_family = address_family = select_address_family(host, port)
        server_address = get_sockaddr(host, int(port), address_family)
        if address_family == af_unix and fd is None:
            server_address = t.cast(str, server_address)

            if os.path.exists(server_address):
                os.unlink(server_address)

        super().__init__(
            server_address,
            handler,
            bind_and_activate=False,
        )

        if fd is None:
            try:
                self.server_bind()
                self.server_activate()
            except BaseException:
                self.server_close()
                raise
        else:
            self.socket = socket.fromfd(fd, address_family, socket.SOCK_STREAM)
            self.server_address = self.socket.getsockname()

        if address_family != af_unix:
            self.port = self.server_address[1]

    def log(self, type: str, message: str, *args: t.Any) -> None:
        _log(type, message, *args)

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        try:
            super().serve_forever(poll_interval=poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.server_close()

    def handle_error(
            self, request: t.Any, client_address: t.Union[t.Tuple[str, int], str]
    ) -> None:
        if self.passthrough_errors:
            raise

        return super().handle_error(request, client_address)

    def log_startup(self) -> None:
        """Show information about the address when starting the server."""
        if self.address_family == af_unix:
            _log("info", f" * Running on {self.host} (Press CTRL+C to quit)")
        else:
            scheme = "http"
            messages = []
            all_addresses_message = (
                f" * Running on all addresses ({self.host})\n"
                "   WARNING: This is a development server. Do not use it in"
                " a production deployment."
            )

            if self.host == "0.0.0.0":
                messages.append(all_addresses_message)
                messages.append(f" * Running on {scheme}://127.0.0.1:{self.port}")
                display_hostname = get_interface_ip(socket.AF_INET)
            elif self.host == "::":
                messages.append(all_addresses_message)
                messages.append(f" * Running on {scheme}://[::1]:{self.port}")
                display_hostname = get_interface_ip(socket.AF_INET6)
            else:
                display_hostname = self.host

            if ":" in display_hostname:
                display_hostname = f"[{display_hostname}]"

            messages.append(
                f" * Running on {scheme}://{display_hostname}:{self.port}"
                " (Press CTRL+C to quit)"
            )
            _log("info", "\n".join(messages))


def make_server(
        host: str,
        port: int,
        app: "WSGIApplication",
        request_handler: t.Optional[t.Type[WSGIRequestHandler]] = None,
        passthrough_errors: bool = False,
        fd: t.Optional[int] = None,
) -> BaseWSGIServer:
    """Create an appropriate WSGI server instance.
    """
    return BaseWSGIServer(
        host, port, app, request_handler, passthrough_errors, fd=fd
    )


def prepare_socket(hostname: str, port: int) -> socket.socket:
    """Prepare a socket for use by the WSGI server"""
    address_family = select_address_family(hostname, port)
    server_address = get_sockaddr(hostname, port, address_family)
    s = socket.socket(address_family, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.set_inheritable(True)

    # Remove the socket file if it already exists.
    if address_family == af_unix:
        server_address = t.cast(str, server_address)

        if os.path.exists(server_address):
            os.unlink(server_address)
    try:
        s.bind(server_address)
    except OSError as e:
        print(e.strerror, file=sys.stderr)

        if e.errno == errno.EADDRINUSE:
            print(
                f"Port {port} is in use by another program. Either"
                " identify and stop that program, or start the"
                " server with a different port.",
                file=sys.stderr,
            )

            if sys.platform == "darwin" and port == 5000:
                print(
                    "On macOS, try disabling the 'AirPlay Receiver'"
                    " service from System Preferences -> Sharing.",
                    file=sys.stderr,
                )

        sys.exit(1)

    s.listen(LISTEN_QUEUE)
    return s


def run_simple(
        hostname: str,
        port: int,
        application: "WSGIApplication",
        request_handler: t.Optional[t.Type[WSGIRequestHandler]] = None,
        static_files: t.Optional[t.Dict[str, t.Union[str, t.Tuple[str, str]]]] = None,
        passthrough_errors: bool = False,
) -> None:
    """Start a server for a WSGI application.

    :param hostname: The host to bind to, for example ``'localhost'``.
        Can be a domain, IPv4 or IPv6 address, or file path starting
        with ``unix://`` for a Unix socket.
    :param port: The port to bind to, for example ``8080``. Using ``0``
        tells the OS to pick a random free port.
    :param application: The WSGI application to run.
    :param extra_files: The reloader will watch these files for changes
        in addition to Python modules. For example, watch a
        configuration file.
    :param request_handler: Use a different
        :class:`~BaseHTTPServer.BaseHTTPRequestHandler` subclass to
        handle requests.
    :param static_files: A dict mapping URL prefixes to directories to
    :param passthrough_errors: Don't catch unhandled exceptions at the
        server level, let the serve crash instead. If ``use_debugger``
        is enabled, the debugger will still catch such errors.
    """
    if not isinstance(port, int):
        raise TypeError("port must be an integer")

    if static_files:
        from .middleware import SharedDataMiddleware

        application = SharedDataMiddleware(application, static_files)

    if not is_running_from_reloader():
        s = prepare_socket(hostname, port)
        fd = s.fileno()
        os.environ["SERVER_FD"] = str(fd)
    else:
        fd = int(os.environ["SERVER_FD"])

    srv = make_server(
        hostname,
        port,
        application,
        request_handler,
        passthrough_errors,
        fd=fd,
    )

    srv.serve_forever()
