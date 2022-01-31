from src.server.datastructures.Header import Headers, _unicodify_header_value, _missing
from src.server.datastructures.ImmutableMultiDict import is_immutable


class ImmutableHeadersMixin:
    """Makes a :class:`Headers` immutable.
    """

    def __delitem__(self, key, **kwargs):
        is_immutable(self)

    def __setitem__(self, key, value):
        is_immutable(self)

    def set(self, _key, _value, **kw):
        is_immutable(self)

    def setlist(self, key, values):
        is_immutable(self)

    def add(self, _key, _value, **kw):
        is_immutable(self)

    def add_header(self, _key, _value, **_kw):
        is_immutable(self)

    def remove(self, key):
        is_immutable(self)

    def extend(self, *args, **kwargs):
        is_immutable(self)

    def update(self, *args, **kwargs):
        is_immutable(self)

    def insert(self, pos, value):
        is_immutable(self)

    def pop(self, key=None, default=_missing):
        is_immutable(self)

    def popitem(self):
        is_immutable(self)

    def setdefault(self, key, default):
        is_immutable(self)

    def setlistdefault(self, key, default):
        is_immutable(self)


class EnvironHeaders(ImmutableHeadersMixin, Headers):
    """Read only version of the headers from a WSGI environment. """

    def __init__(self, environ):
        self.environ = environ

    def __eq__(self, other):
        return self.environ is other.environ

    __hash__ = None

    def __getitem__(self, key, _get_mode=False):
        if not isinstance(key, str):
            raise KeyError(key)
        key = key.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            return _unicodify_header_value(self.environ[key])
        return _unicodify_header_value(self.environ[f"HTTP_{key}"])

    def __len__(self):
        return len(list(iter(self)))

    def __iter__(self):
        for key, value in self.environ.items():
            if key.startswith("HTTP_") and key not in (
                "HTTP_CONTENT_TYPE",
                "HTTP_CONTENT_LENGTH",
            ):
                yield (
                    key[5:].replace("_", "-").title(),
                    _unicodify_header_value(value),
                )
            elif key in ("CONTENT_TYPE", "CONTENT_LENGTH") and value:
                yield key.replace("_", "-").title(), _unicodify_header_value(value)

    def copy(self):
        raise TypeError(f"cannot create {type(self).__name__!r} copies")