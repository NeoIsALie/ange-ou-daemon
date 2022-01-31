import typing as t
import io


class LimitedStream(io.IOBase):
    """Wraps a stream so that it doesn't read more than n bytes.
    :param stream: the stream to wrap.
    :param limit: the limit for the stream, must not be longer than
                  what the string can provide if the stream does not
                  end with `EOF` (like `wsgi.input`)
    """

    def __init__(self, stream: t.IO[bytes], limit: int) -> None:
        self._read = stream.read
        self._readline = stream.readline
        self._pos = 0
        self.limit = limit

    def __iter__(self) -> "LimitedStream":
        return self

    @property
    def is_exhausted(self) -> bool:
        """If the stream is exhausted this attribute is `True`."""
        return self._pos >= self.limit

    def on_exhausted(self) -> bytes:
        """This is called when the stream tries to read past the limit.
        The return value of this function is returned from the reading
        function.
        """
        return self._read(0)

    def on_disconnect(self) -> bytes:
        from src.server.exceptions import ClientDisconnected

        raise ClientDisconnected()

    def exhaust(self, chunk_size: int = 1024 * 64) -> None:
        """Exhaust the stream.  This consumes all the data left until the
        limit is reached.
        :param chunk_size: the size for a chunk.  It will read the chunk
                           until the stream is exhausted and throw away
                           the results.
        """
        to_read = self.limit - self._pos
        chunk = chunk_size
        while to_read > 0:
            chunk = min(to_read, chunk)
            self.read(chunk)
            to_read -= chunk

    def read(self, size: t.Optional[int] = None) -> bytes:
        """Read `size` bytes or if size is not provided everything is read.
        :param size: the number of bytes read.
        """
        if self._pos >= self.limit:
            return self.on_exhausted()
        if size is None or size == -1:  # -1 is for consistence with file
            size = self.limit
        to_read = min(self.limit - self._pos, size)
        try:
            read = self._read(to_read)
        except (OSError, ValueError):
            return self.on_disconnect()
        if to_read and len(read) != to_read:
            return self.on_disconnect()
        self._pos += len(read)
        return read

    def readline(self, size: t.Optional[int] = None) -> bytes:
        """Reads one line from the stream."""
        if self._pos >= self.limit:
            return self.on_exhausted()
        if size is None:
            size = self.limit - self._pos
        else:
            size = min(size, self.limit - self._pos)
        try:
            line = self._readline(size)
        except (ValueError, OSError):
            return self.on_disconnect()
        if size and not line:
            return self.on_disconnect()
        self._pos += len(line)
        return line

    def readlines(self, size: t.Optional[int] = None) -> t.List[bytes]:
        """Reads a file into a list of strings.  It calls :meth:`readline`
        until the file is read to the end.  It does support the optional
        `size` argument if the underlying stream supports it for
        `readline`.
        """
        last_pos = self._pos
        result = []
        if size is not None:
            end = min(self.limit, last_pos + size)
        else:
            end = self.limit
        while True:
            if size is not None:
                size -= last_pos - self._pos
            if self._pos >= end:
                break
            result.append(self.readline(size))
            if size is not None:
                last_pos = self._pos
        return result

    def tell(self) -> int:
        """Returns the position of the stream."""
        return self._pos

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration()
        return line

    def readable(self) -> bool:
        return True
