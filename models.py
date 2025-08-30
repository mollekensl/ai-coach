from sqlalchemy import Column, Integer, String, Float, Date, DateTime, JSON
from db import Base

class TokenStore(Base):
    __tablename__ = "tokens"
    id = Column(Integer, primary_key=True)
    provider = Column(String, unique=True)  # "whoop"
    access_token = Column(String)
    refresh_token = Column(String)
    expires_at = Column(Integer)  # epoch seconds

class DailyRecovery(Base):
    __tablename__ = "daily_recovery"
    id = Column(Integer, primary_key=True)
    date = Column(Date, unique=True)
    hrv_ms = Column(Float)
    rhr_bpm = Column(Float)
    sleep_min = Column(Integer)
    temp_delta = Column(Float)
    raw = Column(JSON)  # store full JSON for traceability
    
class GarminActivity(Base):
    __tablename__ = "garmin_activities"
    id = Column(Integer, primary_key=True)
    activity_id = Column(String, unique=True)
    start_time = Column(DateTime)
    sport = Column(String)
    duration_s = Column(Float)
    distance_m = Column(Float)
    avg_hr = Column(Float)
    max_hr = Column(Float)
    training_load = Column(Float)
    vo2max = Column(Float)
    summary_json = Column(JSON)
