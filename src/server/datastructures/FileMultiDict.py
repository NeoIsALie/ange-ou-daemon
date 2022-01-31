import mimetypes

from src.server.datastructures import FileStorage, MultiDict


class FileMultiDict(MultiDict):
    """A special :class:`MultiDict` that has convenience methods to add
    files to it.
    """

    def add_file(self, name, file, filename=None, content_type=None):
        """Adds a new file to the dict.  `file` can be a file name or
        a :class:`file`-like or a :class:`FileStorage` object.
        :param name: the name of the field.
        :param file: a filename or :class:`file`-like object
        :param filename: an optional filename
        :param content_type: an optional content type
        """
        if isinstance(file, FileStorage):
            value = file
        else:
            if isinstance(file, str):
                if filename is None:
                    filename = file
                file = open(file, "rb")
            if filename and content_type is None:
                content_type = (
                    mimetypes.guess_type(filename)[0] or "application/octet-stream"
                )
            value = FileStorage(file, filename, name, content_type)

        self.add(name, value)
