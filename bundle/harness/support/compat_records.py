from sqlalchemy import Column,Integer,PickleType
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import declarative_base
Base=declarative_base()
class PersistedRecord(Base):
    __tablename__="persisted_records"
    id=Column(Integer,primary_key=True)
    payload=Column(MutableDict.as_mutable(PickleType))
