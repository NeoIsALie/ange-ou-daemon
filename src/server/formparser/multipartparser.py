from itertools import chain

from src.server.datastructures import FileStorage, MultiDict, Headers
import typing as t

from src.server.formparser.utils import default_stream_factory
from src.server.formparser.multipart import Field, MultipartDecoder, File, Epilogue, NeedData, Data
from src.server.http import parse_options_header
from src.server.urls.urls import _make_chunk_iter


class MultiPartParser:
    def __init__(
        self,
        stream_factory: t.Optional["TStreamFactory"] = None,
        charset: str = "utf-8",
        errors: str = "replace",
        max_form_memory_size: t.Optional[int] = None,
        cls: t.Optional[t.Type[MultiDict]] = None,
        buffer_size: int = 64 * 1024,
    ) -> None:
        self.charset = charset
        self.errors = errors
        self.max_form_memory_size = max_form_memory_size

        if stream_factory is None:
            stream_factory = default_stream_factory

        self.stream_factory = stream_factory

        if cls is None:
            cls = MultiDict

        self.cls = cls

        self.buffer_size = buffer_size

    def fail(self, message: str) -> "te.NoReturn":
        raise ValueError(message)

    def get_part_charset(self, headers: Headers) -> str:
        content_type = headers.get("content-type")

        if content_type:
            mimetype, ct_params = parse_options_header(content_type)
            return ct_params.get("charset", self.charset)

        return self.charset

    def start_file_streaming(
        self, event: File, total_content_length: t.Optional[int]
    ) -> t.IO[bytes]:
        content_type = event.headers.get("content-type")

        try:
            content_length = int(event.headers["content-length"])
        except (KeyError, ValueError):
            content_length = 0

        container = self.stream_factory(
            total_content_length=total_content_length,
            filename=event.filename,
            content_type=content_type,
            content_length=content_length,
        )
        return container

    def parse(
        self, stream: t.IO[bytes], boundary: bytes, content_length: t.Optional[int]
    ) -> t.Tuple[MultiDict, MultiDict]:
        container: t.Union[t.IO[bytes], t.List[bytes]]
        _write: t.Callable[[bytes], t.Any]

        iterator = chain(
            _make_chunk_iter(
                stream,
                limit=content_length,
                buffer_size=self.buffer_size,
            ),
            [None],
        )

        parser = MultipartDecoder(boundary, self.max_form_memory_size)

        fields = []
        files = []

        current_part: t.Union[Field, File]
        for data in iterator:
            parser.receive_data(data)
            event = parser.next_event()
            while not isinstance(event, (Epilogue, NeedData)):
                if isinstance(event, Field):
                    current_part = event
                    container = []
                    _write = container.append
                elif isinstance(event, File):
                    current_part = event
                    container = self.start_file_streaming(event, content_length)
                    _write = container.write
                elif isinstance(event, Data):
                    _write(event.data)
                    if not event.more_data:
                        if isinstance(current_part, Field):
                            value = b"".join(container).decode(
                                self.get_part_charset(current_part.headers), self.errors
                            )
                            fields.append((current_part.name, value))
                        else:
                            container = t.cast(t.IO[bytes], container)
                            container.seek(0)
                            files.append(
                                (
                                    current_part.name,
                                    FileStorage(
                                        container,
                                        current_part.filename,
                                        current_part.name,
                                        headers=current_part.headers,
                                    ),
                                )
                            )

                event = parser.next_event()

        return self.cls(fields), self.cls(files)