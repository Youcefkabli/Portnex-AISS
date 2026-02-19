from sqlalchemy import Column, BigInteger, SmallInteger, Float, Double, Text, DateTime, Index
from sqlalchemy.orm import DeclarativeBase
import datetime


class Base(DeclarativeBase):
    pass


class Position1Sec(Base):
    """
    High-frequency table — every position message as received (~1-sec resolution).
    Retained for HIGH_FREQ_RETENTION_HOURS (default 24h), then auto-dropped by
    TimescaleDB retention policy.
    """
    __tablename__ = "positions_1sec"

    time = Column(DateTime(timezone=True), nullable=False, primary_key=True)
    mmsi = Column(BigInteger, nullable=False, primary_key=True)
    latitude = Column(Double)
    longitude = Column(Double)
    speed = Column(Float)
    course = Column(Float)
    heading = Column(SmallInteger)
    nav_status = Column(SmallInteger)
    rot = Column(Float)
    msg_type = Column(SmallInteger)

    __table_args__ = (Index("idx_1sec_mmsi_time", "mmsi", "time"),)


class Position1Min(Base):
    """
    Historical table — one row per vessel per minute (downsampled via continuous
    aggregate from positions_1sec, then materialised here permanently).
    No retention policy — kept forever.
    """
    __tablename__ = "positions_1min"

    time = Column(DateTime(timezone=True), nullable=False, primary_key=True)
    mmsi = Column(BigInteger, nullable=False, primary_key=True)
    latitude = Column(Double)
    longitude = Column(Double)
    speed = Column(Float)
    course = Column(Float)
    heading = Column(SmallInteger)
    nav_status = Column(SmallInteger)
    msg_count = Column(SmallInteger)

    __table_args__ = (Index("idx_1min_mmsi_time", "mmsi", "time"),)


class Vessel(Base):
    """Static & voyage data — upserted on AIS msg type 5 / 24."""
    __tablename__ = "vessels"

    mmsi = Column(BigInteger, primary_key=True)
    imo = Column(BigInteger)
    callsign = Column(Text)
    name = Column(Text)
    ship_type = Column(SmallInteger)
    ship_type_text = Column(Text)
    dim_to_bow = Column(SmallInteger)
    dim_to_stern = Column(SmallInteger)
    dim_to_port = Column(SmallInteger)
    dim_to_starboard = Column(SmallInteger)
    draught = Column(Float)
    destination = Column(Text)
    eta = Column(Text)
    first_seen = Column(DateTime(timezone=True), default=datetime.datetime.utcnow)
    last_updated = Column(
        DateTime(timezone=True),
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )
