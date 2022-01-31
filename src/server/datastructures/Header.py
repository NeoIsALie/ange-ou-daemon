import typing as t
from src.server.datastructures.MultiDict import (
    iter_multi_items,
    MultiDict
)
import src.server.exceptions as exceptions

_token_chars = frozenset(
    "!#$%&'*+-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ^_`abcdefghijklmnopqrstuvwxyz|~"
)

def quote_header_value(
    value: t.Union[str, int], extra_chars: str = "", allow_token: bool = True
) -> str:
    """Quote a header value if necessary.
    :param value: the value to quote.
    :param extra_chars: a list of extra characters to skip quoting.
    :param allow_token: if this is enabled token values are returned
                        unchanged.
    """
    if isinstance(value, bytes):
        value = value.decode("latin1")
    value = str(value)
    if allow_token:
        token_chars = _token_chars | set(extra_chars)
        if set(value).issubset(token_chars):
            return value
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def dump_options_header(
    header: t.Optional[str], options: t.Mapping[str, t.Optional[t.Union[str, int]]]
) -> str:
    """The reverse function to :func:`parse_options_header`.
    :param header: the header to dump
    :param options: a dict of options to append.
    """
    segments = []
    if header is not None:
        segments.append(header)
    for key, value in options.items():
        if value is None:
            segments.append(key)
        else:
            segments.append(f"{key}={quote_header_value(value)}")
    return "; ".join(segments)


def _options_header_vkw(value, kw):
    return dump_options_header(
        value, {k.replace("_", "-"): v for k, v in kw.items()}
    )


def _unicodify_header_value(value):
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    if not isinstance(value, str):
        value = str(value)
    return value


class _Missing:
    def __repr__(self) -> str:
        return "no value"

    def __reduce__(self) -> str:
        return "_missing"


_missing = _Missing()


class Headers:
    """An object that stores some headers."""

    def __init__(self, defaults=None):
        self._list = []
        if defaults is not None:
            self.extend(defaults)

    def __getitem__(self, key, _get_mode=False):
        if not _get_mode:
            if isinstance(key, int):
                return self._list[key]
            elif isinstance(key, slice):
                return self.__class__(self._list[key])
        if not isinstance(key, str):
            raise exceptions.BadRequestKeyError(key)
        ikey = key.lower()
        for k, v in self._list:
            if k.lower() == ikey:
                return v
        if _get_mode:
            raise KeyError()
        raise exceptions.BadRequestKeyError(key)

    def __eq__(self, other):
        def lowered(item):
            return (item[0].lower(),) + item[1:]

        return other.__class__ is self.__class__ and set(
            map(lowered, other._list)
        ) == set(map(lowered, self._list))

    __hash__ = None

    def get(self, key, default=None, type=None, as_bytes=False):
        try:
            rv = self.__getitem__(key, _get_mode=True)
        except KeyError:
            return default
        if as_bytes:
            rv = rv.encode("latin1")
        if type is None:
            return rv
        try:
            return type(rv)
        except ValueError:
            return default

    def getlist(self, key, type=None, as_bytes=False):
        """Return the list of items for a given key. If that key is not in the
        :class:`Headers`, the return value will be an empty list.
        :param key: The key to be looked up.
        :param type: A callable that is used to cast the value in the
                     :class:`Headers`.  If a :exc:`ValueError` is raised
                     by this callable the value will be removed from the list.
        :return: a :class:`list` of all the values for the key.
        :param as_bytes: return bytes instead of strings.
        """
        ikey = key.lower()
        result = []
        for k, v in self:
            if k.lower() == ikey:
                if as_bytes:
                    v = v.encode("latin1")
                if type is not None:
                    try:
                        v = type(v)
                    except ValueError:
                        continue
                result.append(v)
        return result

    def get_all(self, name):
        """Return a list of all the values for the named field."""
        return self.getlist(name)

    def items(self, lower=False):
        for key, value in self:
            if lower:
                key = key.lower()
            yield key, value

    def keys(self, lower=False):
        for key, _ in self.items(lower):
            yield key

    def values(self):
        for _, value in self.items():
            yield value

    def extend(self, *args, **kwargs):
        """Extend headers in this object with items from another object
        containing header items as well as keyword arguments.
        """
        if len(args) > 1:
            raise TypeError(f"update expected at most 1 arguments, got {len(args)}")

        if args:
            for key, value in iter_multi_items(args[0]):
                self.add(key, value)

        for key, value in iter_multi_items(kwargs):
            self.add(key, value)

    def __delitem__(self, key, _index_operation=True):
        if _index_operation and isinstance(key, (int, slice)):
            del self._list[key]
            return
        key = key.lower()
        new = []
        for k, v in self._list:
            if k.lower() != key:
                new.append((k, v))
        self._list[:] = new

    def remove(self, key):
        """Remove a key.
        :param key: The key to be removed.
        """
        return self.__delitem__(key, _index_operation=False)

    def pop(self, key=None, default=_missing):
        """Removes and returns a key or index.
        :param key: The key to be popped.  If this is an integer the item at
                    that position is removed, if it's a string the value for
                    that key is.  If the key is omitted or `None` the last
                    item is removed.
        :return: an item.
        """
        if key is None:
            return self._list.pop()
        if isinstance(key, int):
            return self._list.pop(key)
        try:
            rv = self[key]
            self.remove(key)
        except KeyError:
            if default is not _missing:
                return default
            raise
        return rv

    def popitem(self):
        """Removes a key or index and returns a (key, value) item."""
        return self.pop()

    def __contains__(self, key):
        """Check if a key is present."""
        try:
            self.__getitem__(key, _get_mode=True)
        except KeyError:
            return False
        return True

    def __iter__(self):
        """Yield ``(key, value)`` tuples."""
        return iter(self._list)

    def __len__(self):
        return len(self._list)

    def add(self, _key, _value, **kw):
        """Add a new header tuple to the list."""
        if kw:
            _value = _options_header_vkw(_value, kw)
        _key = _unicodify_header_value(_key)
        _value = _unicodify_header_value(_value)
        self._validate_value(_value)
        self._list.append((_key, _value))

    def _validate_value(self, value):
        if not isinstance(value, str):
            raise TypeError("Value should be a string.")
        if "\n" in value or "\r" in value:
            raise ValueError(
                "Detected newline in header value.  This is "
                "a potential security problem"
            )

    def add_header(self, _key, _value, **_kw):
        """Add a new header tuple to the list."""
        self.add(_key, _value, **_kw)

    def clear(self):
        """Clears all headers."""
        del self._list[:]

    def set(self, _key, _value, **kw):
        """Remove all header tuples for `key` and add a new one.
        :param key: The key to be inserted.
        :param value: The value to be inserted.
        """
        if kw:
            _value = _options_header_vkw(_value, kw)
        _key = _unicodify_header_value(_key)
        _value = _unicodify_header_value(_value)
        self._validate_value(_value)
        if not self._list:
            self._list.append((_key, _value))
            return
        listiter = iter(self._list)
        ikey = _key.lower()
        for idx, (old_key, _old_value) in enumerate(listiter):
            if old_key.lower() == ikey:
                # replace first occurrence
                self._list[idx] = (_key, _value)
                break
        else:
            self._list.append((_key, _value))
            return
        self._list[idx + 1 :] = [t for t in listiter if t[0].lower() != ikey]

    def setlist(self, key, values):
        """Remove any existing values for a header and add new ones.
        :param key: The header key to set.
        :param values: An iterable of values to set for the key.
        """
        if values:
            values_iter = iter(values)
            self.set(key, next(values_iter))

            for value in values_iter:
                self.add(key, value)
        else:
            self.remove(key)

    def setdefault(self, key, default):
        """Return the first value for the key if it is in the headers,
        otherwise set the header to the value given by ``default`` and
        return that.
        :param key: The header key to get.
        :param default: The value to set for the key if it is not in the
            headers.
        """
        if key in self:
            return self[key]

        self.set(key, default)
        return default

    def setlistdefault(self, key, default):
        """Return the list of values for the key if it is in the
        headers, otherwise set the header to the list of values given
        by ``default`` and return that.
        :param key: The header key to get.
        :param default: An iterable of values to set for the key if it
            is not in the headers.
        """
        if key not in self:
            self.setlist(key, default)

        return self.getlist(key)

    def __setitem__(self, key, value):
        if isinstance(key, (slice, int)):
            if isinstance(key, int):
                value = [value]
            value = [
                (_unicodify_header_value(k), _unicodify_header_value(v))
                for (k, v) in value
            ]
            for (_, v) in value:
                self._validate_value(v)
            if isinstance(key, int):
                self._list[key] = value[0]
            else:
                self._list[key] = value
        else:
            self.set(key, value)

    def update(self, *args, **kwargs):
        """Replace headers in this object with items from another
        headers object and keyword arguments.
        """
        if len(args) > 1:
            raise TypeError(f"update expected at most 1 arguments, got {len(args)}")

        if args:
            mapping = args[0]

            if isinstance(mapping, (Headers, MultiDict)):
                for key in mapping.keys():
                    self.setlist(key, mapping.getlist(key))
            elif isinstance(mapping, dict):
                for key, value in mapping.items():
                    if isinstance(value, (list, tuple)):
                        self.setlist(key, value)
                    else:
                        self.set(key, value)
            else:
                for key, value in mapping:
                    self.set(key, value)

        for key, value in kwargs.items():
            if isinstance(value, (list, tuple)):
                self.setlist(key, value)
            else:
                self.set(key, value)

    def to_wsgi_list(self):
        """Convert the headers into a list suitable for WSGI.
        :return: list
        """
        return list(self)

    def copy(self):
        return self.__class__(self._list)

    def __copy__(self):
        return self.copy()

    def __str__(self):
        """Returns formatted headers suitable for HTTP transmission."""
        strs = []
        for key, value in self.to_wsgi_list():
            strs.append(f"{key}: {value}")
        strs.append("\r\n")
        return "\r\n".join(strs)

    def __repr__(self):
        return f"{type(self).__name__}({list(self)!r})"