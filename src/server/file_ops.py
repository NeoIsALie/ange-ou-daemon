import typing as t


def wrap_file(
        environ, file: t.IO[bytes], buffer_size: int = 8192
) -> t.Iterable[bytes]:
    return environ.get("wsgi.file_wrapper", FileWrapper)(  
        file, buffer_size
    )


class FileWrapper:
    """This class can be used to convert a :class:`file`-like object into
    an iterable.  It yields `buffer_size` blocks until the file is fully
    read.
    :param file: a :class:`file`-like object with a :meth:`~file.read` method.
    :param buffer_size: number of bytes for one iteration.
    """

    def __init__(self, file: t.IO[bytes], buffer_size: int = 8192) -> None:
        self.file = file
        self.buffer_size = buffer_size

    def close(self) -> None:
        if hasattr(self.file, "close"):
            self.file.close()

    def seekable(self) -> bool:
        if hasattr(self.file, "seekable"):
            return self.file.seekable()
        if hasattr(self.file, "seek"):
            return True
        return False

    def seek(self, *args: t.Any) -> None:
        if hasattr(self.file, "seek"):
            self.file.seek(*args)

    def tell(self) -> t.Optional[int]:
        if hasattr(self.file, "tell"):
            return self.file.tell()
        return None

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        data = self.file.read(self.buffer_size)
        if data:
            return data
        raise StopIteration()
