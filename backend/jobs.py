from sqlalchemy import create_engine, Column, Integer, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

DATABASE_URL = "sqlite:///app.db"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()


class TranscriptionRecord(Base):
    __tablename__ = "transcriptions"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    transcription = Column(Text, nullable=False)
    llm_result = Column(Text, nullable=True)


def create_tables():
    Base.metadata.create_all(bind=engine)


def create_record(transcription: str, llm_result: str | None = None):
    session = SessionLocal()
    try:
        record = TranscriptionRecord(
            transcription=transcription,
            llm_result=llm_result,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record
    finally:
        session.close()


def get_record_by_id(record_id: int):
    session = SessionLocal()
    try:
        return (
            session.query(TranscriptionRecord)
            .filter(TranscriptionRecord.id == record_id)
            .first()
        )
    finally:
        session.close()


def delete_record_by_id(record_id: int):
    session = SessionLocal()
    try:
        record = (
            session.query(TranscriptionRecord)
            .filter(TranscriptionRecord.id == record_id)
            .first()
        )

        if not record:
            return False

        session.delete(record)
        session.commit()
        return True
    finally:
        session.close()


def list_all_records():
    session = SessionLocal()
    try:
        records = session.query(TranscriptionRecord).all()
        return [
            {
                "id": r.id,
                "timestamp": r.timestamp,
                "transcription": r.transcription,
                "llm_result": r.llm_result,
            }
            for r in records
        ]
    finally:
        session.close()


if __name__ == "__main__":
    create_tables()

    new_record = create_record(
        transcription="Hello world", llm_result="Greeting detected"
    )

    print("Created:", new_record.id)

    record = get_record_by_id(new_record.id)
    print("Fetched:", record.transcription, record.llm_result)

    list_all = list_all_records()
    print(f"Records: {list_all}")

    deleted = delete_record_by_id(new_record.id)
    print("Deleted:", deleted)
