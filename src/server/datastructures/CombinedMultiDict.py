from src.server import exceptions
from src.server.datastructures.ImmutableMultiDict import ImmutableMultiDictMixin
from src.server.datastructures.MultiDict import MultiDict


class CombinedMultiDict(ImmutableMultiDictMixin, MultiDict):
    def __reduce_ex__(self, protocol):
        return type(self), (self.dicts,)

    def __init__(self, dicts=None):
        self.dicts = list(dicts) or []

    @classmethod
    def fromkeys(cls, keys, value=None):
        raise TypeError(f"cannot create {cls.__name__!r} instances by fromkeys")

    def __getitem__(self, key):
        for d in self.dicts:
            if key in d:
                return d[key]
        raise exceptions.BadRequestKeyError(key)

    def get(self, key, default=None, type=None):
        for d in self.dicts:
            if key in d:
                if type is not None:
                    try:
                        return type(d[key])
                    except ValueError:
                        continue
                return d[key]
        return default

    def getlist(self, key, type=None):
        rv = []
        for d in self.dicts:
            rv.extend(d.getlist(key, type))
        return rv

    def _keys_impl(self):
        rv = set()
        rv.update(*self.dicts)
        return rv

    def keys(self):
        return self._keys_impl()

    def __iter__(self):
        return iter(self.keys())

    def items(self, multi=False):
        found = set()
        for d in self.dicts:
            for key, value in d.items(multi):
                if multi:
                    yield key, value
                elif key not in found:
                    found.add(key)
                    yield key, value

    def values(self):
        for _key, value in self.items():
            yield value

    def lists(self):
        rv = {}
        for d in self.dicts:
            for key, values in d.lists():
                rv.setdefault(key, []).extend(values)
        return list(rv.items())

    def listvalues(self):
        return (x[1] for x in self.lists())

    def copy(self):
        """Return a shallow mutable copy of this object. """
        return MultiDict(self)

    def to_dict(self, flat=True):
        """Return the contents as regular dict.  If `flat` is `True` the
        returned dict will only have the first item present, if `flat` is
        `False` all values will be returned as lists.
        :param flat: If set to `False` the dict returned will have lists
                     with all the values in it.  Otherwise it will only
                     contain the first item for each key.
        :return: a :class:`dict`
        """
        if flat:
            return dict(self.items())

        return dict(self.lists())

    def __len__(self):
        return len(self._keys_impl())

    def __contains__(self, key):
        for d in self.dicts:
            if key in d:
                return True
        return False

    def __repr__(self):
        return f"{type(self).__name__}({self.dicts!r})"