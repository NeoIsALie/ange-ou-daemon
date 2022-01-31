from itertools import repeat

from src.server.datastructures.MultiDict import MultiDict


def is_immutable(self):
    raise TypeError(f"{type(self).__name__!r} objects are immutable")


class ImmutableDictMixin:
    _hash_cache = None

    @classmethod
    def fromkeys(cls, keys, value=None):
        instance = super().__new__(cls)
        instance.__init__(zip(keys, repeat(value)))
        return instance

    def __reduce_ex__(self, protocol):
        return type(self), (dict(self),)

    def _iter_hashitems(self):
        return self.items()

    def __hash__(self):
        if self._hash_cache is not None:
            return self._hash_cache
        rv = self._hash_cache = hash(frozenset(self._iter_hashitems()))
        return rv

    def setdefault(self, key, default=None):
        is_immutable(self)

    def update(self, *args, **kwargs):
        is_immutable(self)

    def pop(self, key, default=None):
        is_immutable(self)

    def popitem(self):
        is_immutable(self)

    def __setitem__(self, key, value):
        is_immutable(self)

    def __delitem__(self, key):
        is_immutable(self)

    def clear(self):
        is_immutable(self)


class ImmutableMultiDictMixin(ImmutableDictMixin):
    """Makes a :class:`MultiDict` immutable."""

    def __reduce_ex__(self, protocol):
        return type(self), (list(self.items(multi=True)),)

    def _iter_hashitems(self):
        return self.items(multi=True)

    def add(self, key, value):
        is_immutable(self)

    def popitemlist(self):
        is_immutable(self)

    def poplist(self, key):
        is_immutable(self)

    def setlist(self, key, new_list):
        is_immutable(self)

    def setlistdefault(self, key, default_list=None):
        is_immutable(self)


class ImmutableMultiDict(ImmutableMultiDictMixin, MultiDict):
    def copy(self):
        """Return a shallow mutable copy of this object."""
        return MultiDict(self)

    def __copy__(self):
        return self