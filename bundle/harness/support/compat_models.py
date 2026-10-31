import attr
@attr.s(slots=True)
class SlottedRecord:
    count = attr.ib(type=int)
    name = attr.ib(type=str)
@attr.s(slots=True, frozen=True)
class FrozenRecord:
    count = attr.ib(type=int)
    name = attr.ib(type=str)
@attr.s(slots=True, frozen=True, cache_hash=True)
class CachedRecord:
    count = attr.ib(type=int)
    name = attr.ib(type=str)
