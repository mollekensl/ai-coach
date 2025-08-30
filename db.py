from sqlalchemy import create_engine, Column, Integer, String, Float, Date, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

engine = create_engine("sqlite:///coach.db", future=True)
SessionLocal = sessionmaker(bind=engine, future=True)
Base = declarative_base()
