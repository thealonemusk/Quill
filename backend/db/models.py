from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, ForeignKey, Boolean, text
from sqlalchemy import event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker
from sqlalchemy.sql import func
from datetime import datetime
import uuid

from backend.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False)
    company = Column(String, nullable=False)
    platform = Column(String, nullable=False)  # linkedin, indeed, naukri, instahyre, manual
    url = Column(String, unique=True, nullable=False)
    location = Column(String, default="")
    experience_required = Column(String, default="")
    salary_range = Column(String, default="")
    jd_text = Column(Text, default="")
    # AI analysis results
    relevance_score = Column(Float, default=0.0)
    fit_reason = Column(Text, default="")
    missing_skills = Column(Text, default="")  # JSON list
    seniority_level = Column(String, default="")
    is_engineering_role = Column(Boolean, default=True)
    # Status tracking
    status = Column(String, default="discovered")  # discovered, scored, tailored, applied, interviewing, rejected, offer
    easy_apply = Column(Boolean, default=False)
    recruiter_name = Column(String, default="")
    recruiter_profile = Column(String, default="")
    ats_type = Column(String, default="")  # greenhouse, lever, workday, custom, unknown
    apply_status = Column(String, default="")  # queued, applying, applied, failed, needs_review, skipped
    apply_error = Column(Text, default="")
    # Timestamps
    discovered_at = Column(DateTime, default=func.now())
    visited_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, default=func.now(), onupdate=func.now())

    application = relationship("Application", back_populates="job", uselist=False)


class Application(Base):
    __tablename__ = "applications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    tailored_resume_path = Column(String, default="")
    tailored_resume_text = Column(Text, default="")
    cold_email = Column(Text, default="")
    linkedin_message = Column(Text, default="")
    cover_letter = Column(Text, default="")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=func.now())

    job = relationship("Job", back_populates="application")


class ResumeSend(Base):
    """
    Which resume went to which job — the other half of a callback rate.

    One row per job, overwritten by a later fill, because the resume that
    matters is the last one attached before the user submitted.

    The outcome is copied here from `jobs.status` on every change (see
    `_snapshot_outcomes`), not only read from the job, because the dashboard's
    Clear deletes rejected jobs. Reading the outcome from `jobs` alone would
    quietly drop every rejection and inflate the callback rate. For the same
    reason `job_id` carries no foreign key: this row must outlive its job.
    """

    __tablename__ = "resume_sends"

    job_id = Column(String, primary_key=True)
    company = Column(String, default="")
    title = Column(String, default="")
    status = Column(String, default="")           # copy of jobs.status
    applied_at = Column(DateTime, nullable=True)  # copy of jobs.applied_at
    # Set by sends.record only. No onupdate: copying a status change onto this
    # row would otherwise move sent_at, and with it the no-reply window.
    sent_at = Column(DateTime, default=func.now())
    channel = Column(String, default="")          # headless | extension
    variant = Column(String, default="default")   # tailored | default
    sha256 = Column(String, default="")
    path = Column(String, default="")
    llm_used = Column(Boolean, default=False)
    coverage = Column(Float, nullable=True)       # required-term coverage, whole PDF
    first_glance = Column(Float, nullable=True)   # same, on what is read first
    reordered = Column(Integer, default=0)        # entries whose lead bullet changed
    reworded = Column(Integer, default=0)         # bullets the model rewrote


@event.listens_for(Session, "before_flush")
def _snapshot_outcomes(session, flush_context, instances) -> None:
    """
    Copy a job's status onto its resume send whenever it changes, through any
    code path that goes via the ORM — the dashboard PATCH, the extension's
    "Mark applied", the review queue, the apply engine.

    Only changed jobs: a new one cannot have a send yet (ids are fresh
    uuid4s), and a scan inserts hundreds of them per flush.
    """
    for obj in list(session.dirty):
        if not isinstance(obj, Job):
            continue
        state = inspect(obj)
        if not (state.attrs.status.history.has_changes()
                or state.attrs.applied_at.history.has_changes()):
            continue
        with session.no_autoflush:
            send = session.get(ResumeSend, obj.id)
        if send is not None:
            send.status = obj.status
            send.applied_at = obj.applied_at


class BaseResume(Base):
    __tablename__ = "base_resumes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    content_text = Column(Text, default="")  # parsed LaTeX text
    is_active = Column(Boolean, default=True)
    uploaded_at = Column(DateTime, default=func.now())


class SeenJob(Base):
    """Permanent record of every job URL the scanner has ever surfaced.

    Separate from `jobs` on purpose: `jobs` is a working queue that the scan
    purges, this is the memory that survives the purge. See
    backend/seen_ledger.py for the status semantics.
    """

    __tablename__ = "seen_jobs"

    url_key = Column(String, primary_key=True)  # md5 of the normalized URL
    url = Column(String, nullable=False)
    tc_key = Column(String, default="", index=True)  # md5 of title|company
    title = Column(String, default="")
    company = Column(String, default="")
    platform = Column(String, default="")
    status = Column(String, default="seen", index=True)
    reason = Column(Text, default="")
    first_seen = Column(DateTime, default=func.now())
    last_seen = Column(DateTime, default=func.now(), onupdate=func.now())
    times_seen = Column(Integer, default=1)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_db()
    _seed_seen_ledger()
    print("Database initialized (SQLite)")


def _seed_seen_ledger():
    """Backfill the ledger once, so jobs handled before it existed stay handled."""
    from backend.seen_ledger import backfill_from_jobs

    db = SessionLocal()
    try:
        backfill_from_jobs(db)
    except Exception as exc:  # a failed backfill must not block startup
        print(f"Seen ledger backfill skipped: {exc}")
    finally:
        db.close()


def _migrate_db():
    """Add columns to existing SQLite DBs without Alembic."""
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
        col_names = {row[1] for row in cols}
        if "visited_at" not in col_names:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN visited_at DATETIME"))
            conn.commit()
        extras = {
            "ats_type": "VARCHAR DEFAULT ''",
            "apply_status": "VARCHAR DEFAULT ''",
            "apply_error": "TEXT DEFAULT ''",
        }
        for name, ddl in extras.items():
            if name not in col_names:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}"))
                conn.commit()
        # create_all() never alters a table that already exists.
        send_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(resume_sends)"))}
        send_extras = {
            "company": "VARCHAR DEFAULT ''",
            "title": "VARCHAR DEFAULT ''",
            "status": "VARCHAR DEFAULT ''",
            "applied_at": "DATETIME",
        }
        for name, ddl in send_extras.items():
            if send_cols and name not in send_cols:
                conn.execute(text(f"ALTER TABLE resume_sends ADD COLUMN {name} {ddl}"))
                conn.commit()
        # The jobs list filters and sorts on these. create_all does not add
        # an index to a table that already exists.
        for stmt in (
            "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)",
            "CREATE INDEX IF NOT EXISTS ix_jobs_platform ON jobs (platform)",
            "CREATE INDEX IF NOT EXISTS ix_jobs_discovered_at ON jobs (discovered_at)",
            "CREATE INDEX IF NOT EXISTS ix_jobs_score ON jobs (relevance_score)",
        ):
            conn.execute(text(stmt))
        conn.commit()
    _release_cleared_jobs()


def _release_cleared_jobs() -> None:
    """
    Undo the blocklist entries that tidying the dashboard used to create.

    Clear recorded everything it removed as "dismissed", a blocking status, so
    each tidy-up permanently hid that whole page of jobs from every future
    scan. On this database that was 235 rows — which is why a scan that found
    hundreds of postings was inserting about twenty.

    Only rows written by Clear are touched, matched on their exact reason
    string; a job genuinely dismissed by hand stays dismissed. Idempotent.
    """
    from sqlalchemy import text as _text

    with engine.connect() as conn:
        try:
            result = conn.execute(
                _text(
                    "UPDATE seen_jobs SET status = 'cleared' "
                    "WHERE status = 'dismissed' AND reason = :reason"
                ),
                {"reason": "cleared from the dashboard"},
            )
            conn.commit()
        except Exception as exc:  # a failed repair must not block startup
            print(f"Seen ledger repair skipped: {exc}")
            return
    if result.rowcount:
        print(
            f"Seen ledger: released {result.rowcount} job(s) blocked by an old "
            f"Clear; they can be rediscovered by the next scan."
        )
