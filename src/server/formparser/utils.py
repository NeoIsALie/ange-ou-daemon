from io import BytesIO
from tempfile import SpooledTemporaryFile, TemporaryFile
import typing as t


def default_stream_factory(
        total_content_length: t.Optional[int],
        content_type: t.Optional[str],
        filename: t.Optional[str],
        content_length: t.Optional[int] = None,
) -> t.IO[bytes]:
    max_size = 1024 * 500

    if SpooledTemporaryFile is not None:
        return t.cast(t.IO[bytes], SpooledTemporaryFile(max_size=max_size, mode="rb+"))
    elif total_content_length is None or total_content_length > max_size:
        return t.cast(t.IO[bytes], TemporaryFile("rb+"))

    return BytesIO()
