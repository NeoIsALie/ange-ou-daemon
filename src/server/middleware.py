import mimetypes
import os
import pkgutil
import posixpath
from datetime import datetime, timezone, time
import typing as t
from io import BytesIO

from src.server.file_ops import wrap_file
from src.server.http import http_date
from src.server.requests.base_response import get_content_type
from src.server.urls.urls import _to_str

_TOpener = t.Callable[[], t.Tuple[t.BinaryIO, datetime, int]]
_TLoader = t.Callable[[t.Optional[str]], t.Tuple[t.Optional[str], t.Optional[_TOpener]]]

_os_alt_seps: t.List[str] = list(
    sep for sep in [os.path.sep, os.path.altsep] if sep is not None and sep != "/"
)


def safe_join(directory: str, *pathnames: str) -> t.Optional[str]:
    """Safely join zero or more untrusted path components to a base
    directory to avoid escaping the base directory.
    :param directory: The trusted base directory.
    :param pathnames: The untrusted path components relative to the
        base directory.
    :return: A safe path, otherwise ``None``.
    """
    parts = [directory]

    for filename in pathnames:
        if filename != "":
            filename = posixpath.normpath(filename)

        if (
                any(sep in filename for sep in _os_alt_seps)
                or os.path.isabs(filename)
                or filename == ".."
                or filename.startswith("../")
        ):
            return None

        parts.append(filename)

    return posixpath.join(*parts)


def get_path_info(
        environ: "WSGIEnvironment", charset: str = "utf-8", errors: str = "replace"
) -> str:
    """Return the ``PATH_INFO``.
    :param environ: WSGI environment to get the path from.
    :param charset: The charset for the path info, or ``None`` if no
        decoding should be performed.
    :param errors: The decoding error handling.
    """
    path = environ.get("PATH_INFO", "").encode("latin1")
    return _to_str(path, charset, errors, allow_none_charset=True)  


class SharedDataMiddleware:
    """A WSGI middleware"
    :param app: the application to wrap.  If you don't want to wrap an
                application you can pass it :exc:`NotFound`.
    :param exports: a list or dict of exported files and folders.
    :param disallow: a list of :func:`~fnmatch.fnmatch` rules.
    :param cache: enable or disable caching headers.
    :param cache_timeout: the cache timeout in seconds for the headers.
    :param fallback_mimetype: The fallback mimetype for unknown files.
    """

    def __init__(
            self,
            app,
            exports: t.Union[
                t.Dict[str, t.Union[str, t.Tuple[str, str]]],
                t.Iterable[t.Tuple[str, t.Union[str, t.Tuple[str, str]]]],
            ],
            disallow: None = None,
            cache: bool = True,
            cache_timeout: int = 60 * 60 * 12,
            fallback_mimetype: str = "application/octet-stream",
    ) -> None:
        self.app = app
        self.exports: t.List[t.Tuple[str, _TLoader]] = []
        self.cache = cache
        self.cache_timeout = cache_timeout

        if isinstance(exports, dict):
            exports = exports.items()

        for key, value in exports:
            if isinstance(value, tuple):
                loader = self.get_package_loader(*value)
            elif isinstance(value, str):
                if os.path.isfile(value):
                    loader = self.get_file_loader(value)
                else:
                    loader = self.get_directory_loader(value)
            else:
                raise TypeError(f"unknown def {value!r}")

            self.exports.append((key, loader))

        if disallow is not None:
            from fnmatch import fnmatch

            self.is_allowed = lambda x: not fnmatch(x, disallow)

        self.fallback_mimetype = fallback_mimetype

    def _opener(self, filename: str) -> _TOpener:
        return lambda: (
            open(filename, "rb"),
            datetime.fromtimestamp(os.path.getmtime(filename), tz=timezone.utc),
            int(os.path.getsize(filename)),
        )

    def get_file_loader(self, filename: str) -> _TLoader:
        return lambda x: (os.path.basename(filename), self._opener(filename))

    def get_package_loader(self, package: str, package_path: str) -> _TLoader:
        load_time = datetime.now(timezone.utc)
        provider = pkgutil.get_loader(package)

        if hasattr(provider, "get_resource_reader"):
            reader = provider.get_resource_reader(package)  

            def loader(
                    path: t.Optional[str],
            ) -> t.Tuple[t.Optional[str], t.Optional[_TOpener]]:
                if path is None:
                    return None, None

                path = safe_join(package_path, path)

                if path is None:
                    return None, None

                basename = posixpath.basename(path)

                try:
                    resource = reader.open_resource(path)
                except OSError:
                    return None, None

                if isinstance(resource, BytesIO):
                    return (
                        basename,
                        lambda: (resource, load_time, len(resource.getvalue())),
                    )

                return (
                    basename,
                    lambda: (
                        resource,
                        datetime.fromtimestamp(
                            os.path.getmtime(resource.name), tz=timezone.utc
                        ),
                        os.path.getsize(resource.name),
                    ),
                )

        else:
            # Python 3.6
            package_filename = provider.get_filename(package)  
            is_filesystem = os.path.exists(package_filename)
            root = os.path.join(os.path.dirname(package_filename), package_path)

            def loader(
                    path: t.Optional[str],
            ) -> t.Tuple[t.Optional[str], t.Optional[_TOpener]]:
                if path is None:
                    return None, None

                path = safe_join(root, path)

                if path is None:
                    return None, None

                basename = posixpath.basename(path)

                if is_filesystem:
                    if not os.path.isfile(path):
                        return None, None

                    return basename, self._opener(path)

                try:
                    data = provider.get_data(path)  
                except OSError:
                    return None, None

                return basename, lambda: (BytesIO(data), load_time, len(data))

        return loader

    def get_directory_loader(self, directory: str) -> _TLoader:
        def loader(
                path: t.Optional[str],
        ) -> t.Tuple[t.Optional[str], t.Optional[_TOpener]]:
            if path is not None:
                path = safe_join(directory, path)

                if path is None:
                    return None, None
            else:
                path = directory

            if os.path.isfile(path):
                return os.path.basename(path), self._opener(path)

            return None, None

        return loader

    def __call__(
            self, environ: "WSGIEnvironment", start_response: "StartResponse"
    ) -> t.Iterable[bytes]:
        path = get_path_info(environ)
        file_loader = None

        for search_path, loader in self.exports:
            if search_path == path:
                real_filename, file_loader = loader(None)

                if file_loader is not None:
                    break

            if not search_path.endswith("/"):
                search_path += "/"

            if path.startswith(search_path):
                real_filename, file_loader = loader(path[len(search_path):])

                if file_loader is not None:
                    break

        if file_loader is None or not self.is_allowed(real_filename):  
            return self.app(environ, start_response)

        guessed_type = mimetypes.guess_type(real_filename)  
        mime_type = get_content_type(guessed_type[0] or self.fallback_mimetype, "utf-8")
        f, mtime, file_size = file_loader()

        headers = [("Date", http_date())]

        if self.cache:
            timeout = self.cache_timeout
            headers += [
                ("Cache-Control", f"max-age={timeout}, public"),
            ]

            headers.append(("Expires", http_date(time() + timeout)))
        else:
            headers.append(("Cache-Control", "public"))

        headers.extend(
            (
                ("Content-Type", mime_type),
                ("Content-Length", str(file_size)),
                ("Last-Modified", http_date(mtime)),
            )
        )
        start_response("200 OK", headers)
        return wrap_file(environ, f)
