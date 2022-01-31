import os
from io import BytesIO

from src.server import http
from src.server.datastructures.Header import Headers


class FileStorage:
    """The :class:`FileStorage` class is a thin wrapper over incoming files.
    """

    def __init__(
            self,
            stream=None,
            filename=None,
            name=None,
            content_type=None,
            content_length=None,
            headers=None,
    ):
        self.name = name
        self.stream = stream or BytesIO()

        if filename is None:
            filename = getattr(stream, "name", None)

            if filename is not None:
                filename = os.fsdecode(filename)

            if filename and filename[0] == "<" and filename[-1] == ">":
                filename = None
        else:
            filename = os.fsdecode(filename)

        self.filename = filename

        if headers is None:
            headers = Headers()
        self.headers = headers
        if content_type is not None:
            headers["Content-Type"] = content_type
        if content_length is not None:
            headers["Content-Length"] = str(content_length)

    def _parse_content_type(self):
        if not hasattr(self, "_parsed_content_type"):
            self._parsed_content_type = http.parse_options_header(self.content_type)

    @property
    def content_type(self):
        """The content-type sent in the header."""
        return self.headers.get("content-type")

    @property
    def content_length(self):
        """The content-length sent in the header."""
        return int(self.headers.get("content-length") or 0)

    @property
    def mimetype(self):
        """Like :attr:`content_type`, but without parameters
        """
        self._parse_content_type()
        return self._parsed_content_type[0].lower()

    @property
    def mimetype_params(self):
        """The mimetype parameters as dict.
        """
        self._parse_content_type()
        return self._parsed_content_type[1]

    def save(self, dst, buffer_size=16384):
        """Save the file to a destination path or file object.
        For secure file saving also have a look at :func:`secure_filename`.
        :param dst: a filename, :class:`os.PathLike`, or open file
            object to write to.
        :param buffer_size: Passed as the ``length``
        """
        from shutil import copyfileobj

        close_dst = False

        if hasattr(dst, "__fspath__"):
            dst = os.fspath(dst)

        if isinstance(dst, str):
            dst = open(dst, "wb")
            close_dst = True

        try:
            copyfileobj(self.stream, dst, buffer_size)
        finally:
            if close_dst:
                dst.close()

    def close(self):
        """Close the underlying file if possible."""
        try:
            self.stream.close()
        except Exception:
            pass

    def __bool__(self):
        return bool(self.filename)

    def __getattr__(self, name):
        try:
            return getattr(self.stream, name)
        except AttributeError:
            if hasattr(self.stream, "_file"):
                return getattr(self.stream._file, name)
            raise

    def __iter__(self):
        return iter(self.stream)

    def __repr__(self):
        return f"<{type(self).__name__}: {self.filename!r} ({self.content_type!r})>"
