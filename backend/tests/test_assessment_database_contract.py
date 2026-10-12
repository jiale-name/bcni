from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects import mysql

from app.models import Base
from tests.conftest import requires_db


EXPECTED_TABLES = {
    "assessment_paper",
    "assessment_task",
    "assessment_score",
    "assessment_attempt",
    "assessment_answer",
    "assessment_task_candidate",
    "assessment_monitor_rule",
    "assessment_notification",
    "outbox_event",
    "assessment_event_review",
    "assessment_paper_question",
    "assessment_event",
}


def test_assessment_paper_matches_frozen_columns() -> None:
    table = Base.metadata.tables["assessment_paper"]
    expected = {
        "id",
        "tenant_id",
        "org_id",
        "paper_code",
        "version_no",
        "name",
        "paper_type",
        "target_level",
        "duration_min",
        "total_score",
        "random_seed",
        "rules",
        "status",
        "published_at",
        "snapshot_hash",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "row_version",
        "deleted_at",
    }
    assert {column.name for column in table.c} == expected
    assert isinstance(table.c.id.type, mysql.BIGINT)
    assert table.c.id.type.unsigned is True
    assert isinstance(table.c.org_id.type, mysql.BIGINT)
    assert table.c.org_id.type.unsigned is True
    assert table.c.version_no.type.unsigned is True
    assert table.c.duration_min.type.unsigned is True
    assert table.c.total_score.type.precision == 8
    assert table.c.total_score.type.scale == 2
    assert table.c.random_seed.nullable is True
    assert table.c.published_at.nullable is True
    assert table.c.snapshot_hash.nullable is True
    assert table.c.row_version.server_default.arg.text == "1"
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_paper_code_version"
        and [column.name for column in constraint.columns]
        == ["tenant_id", "paper_code", "version_no"]
        for constraint in table.constraints
    )


def test_batch_one_registers_all_assessment_tables() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables)


def test_assessment_task_matches_frozen_columns() -> None:
    table = Base.metadata.tables["assessment_task"]
    expected = {
        "id",
        "tenant_id",
        "org_id",
        "task_code",
        "name",
        "paper_id",
        "start_at",
        "deadline_at",
        "duration_min",
        "attempt_limit",
        "target_scope",
        "notify_rules",
        "status",
        "withdrawn_at",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "row_version",
        "deleted_at",
    }
    assert {column.name for column in table.c} == expected
    assert isinstance(table.c.id.type, mysql.BIGINT)
    assert table.c.id.type.unsigned is True
    assert isinstance(table.c.start_at.type, DateTime)
    assert table.c.start_at.type.fsp == 3
    assert isinstance(table.c.duration_min.type, mysql.SMALLINT)
    assert table.c.duration_min.type.unsigned is True
    assert table.c.duration_min.nullable is False
    assert table.c.attempt_limit.server_default.arg.text == "1"
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_task_code"
        and [column.name for column in constraint.columns] == ["tenant_id", "task_code"]
        for constraint in table.constraints
    )
    assert any(isinstance(constraint, CheckConstraint) for constraint in table.constraints)


def test_assessment_score_uses_attempt_rule_score_key_unique_constraint() -> None:
    table = Base.metadata.tables["assessment_score"]
    assert table.c.report_id.nullable is True
    assert isinstance(table.c.score.type, mysql.DECIMAL)
    assert (table.c.score.type.precision, table.c.score.type.scale) == (8, 2)
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_score_attempt_rule_key"
        and [column.name for column in constraint.columns]
        == ["attempt_id", "rule_version", "score_key"]
        for constraint in table.constraints
    )
    assert not any(
        isinstance(constraint, UniqueConstraint) and "report_id" in [column.name for column in constraint.columns]
        for constraint in table.constraints
    )


def test_assessment_tables_use_unsigned_ids_and_millisecond_datetimes() -> None:
    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables[table_name]
        assert isinstance(table.c.id.type, mysql.BIGINT)
        assert table.c.id.type.unsigned is True
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.fsp == 3, f"{table_name}.{column.name} must use DATETIME(3)"


def test_assessment_score_has_no_float_score_column() -> None:
    table = Base.metadata.tables["assessment_score"]
    assert isinstance(table.c.score.type, mysql.DECIMAL)
    assert isinstance(table.c.max_score.type, mysql.DECIMAL)
    assert Decimal("0.00") == Decimal("0")


def test_assessment_event_review_uses_frozen_unique_constraint() -> None:
    table = Base.metadata.tables["assessment_event_review"]
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_event_review"
        and [column.name for column in constraint.columns]
        == ["event_id", "reviewer_id", "reviewed_at"]
        for constraint in table.constraints
    )


def test_assessment_monitor_rule_updated_by_is_required() -> None:
    table = Base.metadata.tables["assessment_monitor_rule"]
    assert table.c.updated_by.nullable is False


def test_assessment_paper_question_is_registered() -> None:
    assert "assessment_paper_question" in Base.metadata.tables


def test_assessment_paper_question_has_exactly_frozen_columns() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert {column.name for column in table.c} == {
        "id",
        "paper_id",
        "question_id",
        "question_version",
        "question_snapshot",
        "seq_no",
        "score",
        "dimension_snapshot",
        "answer_rule_snapshot",
    }


def test_assessment_paper_question_uses_unsigned_identifier_types() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    for name in ("id", "paper_id", "question_id"):
        assert isinstance(table.c[name].type, mysql.BIGINT)
        assert table.c[name].type.unsigned is True
    assert isinstance(table.c.question_version.type, mysql.INTEGER)
    assert table.c.question_version.type.unsigned is True
    assert isinstance(table.c.seq_no.type, mysql.SMALLINT)
    assert table.c.seq_no.type.unsigned is True


def test_assessment_paper_question_uses_frozen_scalar_types() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert isinstance(table.c.score.type, mysql.DECIMAL)
    assert (table.c.score.type.precision, table.c.score.type.scale) == (8, 2)


def test_assessment_paper_question_uses_json_snapshots() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    for name in ("question_snapshot", "dimension_snapshot", "answer_rule_snapshot"):
        assert isinstance(table.c[name].type, mysql.JSON)


def test_assessment_paper_question_columns_are_not_nullable() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert all(column.nullable is False for column in table.c)


def test_assessment_paper_question_has_id_primary_key() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert [column.name for column in table.primary_key.columns] == ["id"]


def test_assessment_paper_question_has_sequence_unique_constraint() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_paper_question_seq"
        and [column.name for column in constraint.columns] == ["paper_id", "seq_no"]
        for constraint in table.constraints
    )


def test_assessment_paper_question_has_version_unique_constraint() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_paper_question_version"
        and [column.name for column in constraint.columns]
        == ["paper_id", "question_id", "question_version"]
        for constraint in table.constraints
    )


def test_assessment_paper_question_has_required_sequence_index() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert any(
        isinstance(index, Index)
        and [column.name for column in index.columns] == ["paper_id", "seq_no"]
        and index.unique is False
        for index in table.indexes
    )


def test_assessment_paper_question_has_no_foreign_keys() -> None:
    table = Base.metadata.tables["assessment_paper_question"]
    assert not any(isinstance(constraint, ForeignKeyConstraint) for constraint in table.constraints)


def test_assessment_attempt_is_registered() -> None:
    assert "assessment_attempt" in Base.metadata.tables


def test_assessment_attempt_has_exactly_frozen_columns() -> None:
    table = Base.metadata.tables["assessment_attempt"]
    assert {column.name for column in table.c} == {
        "id",
        "tenant_id",
        "task_id",
        "talent_id",
        "user_id",
        "paper_snapshot",
        "attempt_no",
        "answer_version",
        "started_at",
        "duration_min_snapshot",
        "deadline_at_snapshot",
        "last_saved_at",
        "submitted_at",
        "status",
        "client_device_hash",
    }


def test_assessment_attempt_has_frozen_types() -> None:
    table = Base.metadata.tables["assessment_attempt"]
    for name in ("id", "tenant_id", "task_id", "talent_id", "user_id"):
        assert isinstance(table.c[name].type, mysql.BIGINT)
        assert table.c[name].type.unsigned is True
    assert isinstance(table.c.attempt_no.type, mysql.TINYINT)
    assert table.c.attempt_no.type.unsigned is True
    assert isinstance(table.c.answer_version.type, mysql.INTEGER)
    assert table.c.answer_version.type.unsigned is True
    assert isinstance(table.c.duration_min_snapshot.type, mysql.SMALLINT)
    assert table.c.duration_min_snapshot.type.unsigned is True
    assert isinstance(table.c.paper_snapshot.type, mysql.JSON)
    assert isinstance(table.c.status.type, String)
    assert table.c.status.type.length == 24
    assert isinstance(table.c.client_device_hash.type, CHAR)
    assert table.c.client_device_hash.type.length == 64
    for name in ("started_at", "deadline_at_snapshot", "last_saved_at", "submitted_at"):
        assert isinstance(table.c[name].type, DateTime)
        assert table.c[name].type.fsp == 3


def test_assessment_attempt_has_frozen_nullability_and_default() -> None:
    table = Base.metadata.tables["assessment_attempt"]
    nullable = {
        name: table.c[name].nullable
        for name in (
            "id",
            "tenant_id",
            "task_id",
            "talent_id",
            "user_id",
            "paper_snapshot",
            "attempt_no",
            "answer_version",
            "started_at",
            "duration_min_snapshot",
            "deadline_at_snapshot",
            "last_saved_at",
            "submitted_at",
            "status",
            "client_device_hash",
        )
    }
    assert nullable == {
        "id": False,
        "tenant_id": False,
        "task_id": False,
        "talent_id": False,
        "user_id": False,
        "paper_snapshot": False,
        "attempt_no": False,
        "answer_version": False,
        "started_at": True,
        "duration_min_snapshot": False,
        "deadline_at_snapshot": False,
        "last_saved_at": True,
        "submitted_at": True,
        "status": False,
        "client_device_hash": True,
    }
    assert table.c.answer_version.server_default is not None
    assert table.c.answer_version.server_default.arg.text == "0"


def test_assessment_attempt_has_primary_key_and_unique_constraint() -> None:
    table = Base.metadata.tables["assessment_attempt"]
    assert [column.name for column in table.primary_key.columns] == ["id"]
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_attempt_task_user_no"
        and [column.name for column in constraint.columns]
        == ["task_id", "user_id", "attempt_no"]
        for constraint in table.constraints
    )


def test_assessment_attempt_has_only_frozen_index() -> None:
    table = Base.metadata.tables["assessment_attempt"]
    assert {
        (index.name, tuple(column.name for column in index.columns), index.unique)
        for index in table.indexes
    } == {
        ("idx_attempt_task_status_saved", ("task_id", "status", "last_saved_at"), False)
    }


def test_assessment_attempt_has_no_foreign_keys() -> None:
    table = Base.metadata.tables["assessment_attempt"]
    assert not any(isinstance(constraint, ForeignKeyConstraint) for constraint in table.constraints)


def test_assessment_answer_is_registered() -> None:
    assert "assessment_answer" in Base.metadata.tables


def test_assessment_answer_has_exactly_frozen_columns() -> None:
    table = Base.metadata.tables["assessment_answer"]
    assert {column.name for column in table.c} == {
        "id",
        "attempt_id",
        "paper_question_id",
        "answer_version",
        "answer",
        "answered_at",
        "objective_score",
        "subjective_ai_score",
        "final_score",
        "score_source",
        "review_status",
        "review_comment",
    }


def test_assessment_answer_has_frozen_types() -> None:
    table = Base.metadata.tables["assessment_answer"]
    for name in ("id", "attempt_id", "paper_question_id"):
        assert isinstance(table.c[name].type, mysql.BIGINT)
        assert table.c[name].type.unsigned is True
    assert isinstance(table.c.answer_version.type, mysql.INTEGER)
    assert table.c.answer_version.type.unsigned is True
    assert isinstance(table.c.answer.type, mysql.JSON)
    for name in ("answered_at",):
        assert isinstance(table.c[name].type, DateTime)
        assert table.c[name].type.fsp == 3
    for name in ("objective_score", "subjective_ai_score", "final_score"):
        assert isinstance(table.c[name].type, mysql.DECIMAL)
        assert (table.c[name].type.precision, table.c[name].type.scale) == (8, 2)
    assert isinstance(table.c.score_source.type, String)
    assert table.c.score_source.type.length == 24
    assert isinstance(table.c.review_status.type, String)
    assert table.c.review_status.type.length == 24
    assert isinstance(table.c.review_comment.type, String)
    assert table.c.review_comment.type.length == 1000


def test_assessment_answer_has_frozen_nullability_and_no_defaults() -> None:
    table = Base.metadata.tables["assessment_answer"]
    assert {
        name: table.c[name].nullable
        for name in table.c.keys()
    } == {
        "id": False,
        "attempt_id": False,
        "paper_question_id": False,
        "answer_version": False,
        "answer": True,
        "answered_at": True,
        "objective_score": True,
        "subjective_ai_score": True,
        "final_score": True,
        "score_source": True,
        "review_status": False,
        "review_comment": True,
    }
    assert all(column.server_default is None for column in table.c)


def test_assessment_answer_has_primary_key_and_current_answer_unique_constraint() -> None:
    table = Base.metadata.tables["assessment_answer"]
    assert [column.name for column in table.primary_key.columns] == ["id"]
    assert {
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {
        ("uk_attempt_question_answer", ("attempt_id", "paper_question_id"))
    }


def test_assessment_answer_has_no_duplicate_index_or_foreign_key() -> None:
    table = Base.metadata.tables["assessment_answer"]
    assert table.indexes == set()
    assert not any(isinstance(constraint, ForeignKeyConstraint) for constraint in table.constraints)


def test_assessment_event_is_registered() -> None:
    assert "assessment_event" in Base.metadata.tables


def test_assessment_event_has_exactly_frozen_columns() -> None:
    table = Base.metadata.tables["assessment_event"]
    assert {column.name for column in table.c} == {
        "id",
        "tenant_id",
        "attempt_id",
        "client_event_id",
        "event_type",
        "occurred_at",
        "received_at",
        "duration_ms",
        "metadata_summary",
        "severity",
        "review_status",
        "reviewed_by",
        "reviewed_at",
    }


def test_assessment_event_has_frozen_types() -> None:
    table = Base.metadata.tables["assessment_event"]
    for name in ("id", "tenant_id", "attempt_id", "reviewed_by"):
        assert isinstance(table.c[name].type, mysql.BIGINT)
        assert table.c[name].type.unsigned is True
    assert isinstance(table.c.client_event_id.type, String)
    assert table.c.client_event_id.type.length == 64
    assert isinstance(table.c.event_type.type, String)
    assert table.c.event_type.type.length == 32
    assert isinstance(table.c.duration_ms.type, mysql.INTEGER)
    assert table.c.duration_ms.type.unsigned is True
    assert isinstance(table.c.metadata_summary.type, mysql.JSON)
    assert isinstance(table.c.severity.type, String)
    assert table.c.severity.type.length == 16
    assert isinstance(table.c.review_status.type, String)
    assert table.c.review_status.type.length == 24
    for name in ("occurred_at", "received_at", "reviewed_at"):
        assert isinstance(table.c[name].type, DateTime)
        assert table.c[name].type.fsp == 3


def test_assessment_event_has_frozen_nullability_and_no_defaults() -> None:
    table = Base.metadata.tables["assessment_event"]
    assert {
        name: table.c[name].nullable
        for name in table.c.keys()
    } == {
        "id": False,
        "tenant_id": False,
        "attempt_id": False,
        "client_event_id": False,
        "event_type": False,
        "occurred_at": False,
        "received_at": False,
        "duration_ms": True,
        "metadata_summary": False,
        "severity": False,
        "review_status": False,
        "reviewed_by": True,
        "reviewed_at": True,
    }
    assert all(column.server_default is None for column in table.c)


def test_assessment_event_has_primary_key_and_client_event_unique_constraint() -> None:
    table = Base.metadata.tables["assessment_event"]
    assert [column.name for column in table.primary_key.columns] == ["id"]
    assert {
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {
        ("uk_attempt_client_event", ("tenant_id", "attempt_id", "client_event_id"))
    }


def test_assessment_event_has_only_frozen_index() -> None:
    table = Base.metadata.tables["assessment_event"]
    assert {
        (index.name, tuple(column.name for column in index.columns), index.unique)
        for index in table.indexes
    } == {
        ("idx_attempt_event_received", ("attempt_id", "received_at", "event_type"), False)
    }


def test_assessment_event_has_no_foreign_keys() -> None:
    table = Base.metadata.tables["assessment_event"]
    assert not any(isinstance(constraint, ForeignKeyConstraint) for constraint in table.constraints)


@requires_db
def test_assessment_answer_database_matches_metadata() -> None:
    from sqlalchemy import create_engine, inspect

    from app.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        inspector = inspect(engine)
        assert inspector.has_table("assessment_answer")
        table = Base.metadata.tables["assessment_answer"]
        db_columns = inspector.get_columns("assessment_answer")
        assert {column["name"] for column in db_columns} == {column.name for column in table.c}
        db_by_name = {column["name"]: column for column in db_columns}
        for name in ("id", "attempt_id", "paper_question_id"):
            assert isinstance(db_by_name[name]["type"], mysql.BIGINT)
            assert db_by_name[name]["type"].unsigned is True
        assert isinstance(db_by_name["answer_version"]["type"], mysql.INTEGER)
        assert db_by_name["answer_version"]["type"].unsigned is True
        assert isinstance(db_by_name["answer"]["type"], mysql.JSON)
        assert isinstance(db_by_name["answered_at"]["type"], mysql.DATETIME)
        assert db_by_name["answered_at"]["type"].fsp == 3
        for name in ("objective_score", "subjective_ai_score", "final_score"):
            assert isinstance(db_by_name[name]["type"], mysql.DECIMAL)
            assert (db_by_name[name]["type"].precision, db_by_name[name]["type"].scale) == (8, 2)
        assert isinstance(db_by_name["score_source"]["type"], mysql.VARCHAR)
        assert db_by_name["score_source"]["type"].length == 24
        assert isinstance(db_by_name["review_status"]["type"], mysql.VARCHAR)
        assert db_by_name["review_status"]["type"].length == 24
        assert isinstance(db_by_name["review_comment"]["type"], mysql.VARCHAR)
        assert db_by_name["review_comment"]["type"].length == 1000
        assert all(
            db_by_name[name]["nullable"] is table.c[name].nullable
            for name in table.c.keys()
        )
        assert all(db_by_name[name]["default"] is None for name in table.c.keys())
        assert inspector.get_pk_constraint("assessment_answer")["constrained_columns"] == ["id"]
        unique_constraints = inspector.get_unique_constraints("assessment_answer")
        assert {
            (item["name"], tuple(item["column_names"]))
            for item in unique_constraints
        } == {
            ("uk_attempt_question_answer", ("attempt_id", "paper_question_id"))
        }
        assert inspector.get_indexes("assessment_answer") == [
            {
                "name": "uk_attempt_question_answer",
                "column_names": ["attempt_id", "paper_question_id"],
                "unique": True,
                "type": "UNIQUE",
            }
        ]
        assert inspector.get_foreign_keys("assessment_answer") == []
    finally:
        engine.dispose()


@requires_db
def test_assessment_event_database_matches_metadata() -> None:
    from sqlalchemy import create_engine, inspect

    from app.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        inspector = inspect(engine)
        assert inspector.has_table("assessment_event")
        table = Base.metadata.tables["assessment_event"]
        db_columns = inspector.get_columns("assessment_event")
        assert {column["name"] for column in db_columns} == {column.name for column in table.c}
        db_by_name = {column["name"]: column for column in db_columns}
        for name in ("id", "tenant_id", "attempt_id", "reviewed_by"):
            assert isinstance(db_by_name[name]["type"], mysql.BIGINT)
            assert db_by_name[name]["type"].unsigned is True
        assert isinstance(db_by_name["client_event_id"]["type"], mysql.VARCHAR)
        assert db_by_name["client_event_id"]["type"].length == 64
        assert isinstance(db_by_name["event_type"]["type"], mysql.VARCHAR)
        assert db_by_name["event_type"]["type"].length == 32
        assert isinstance(db_by_name["duration_ms"]["type"], mysql.INTEGER)
        assert db_by_name["duration_ms"]["type"].unsigned is True
        assert isinstance(db_by_name["metadata_summary"]["type"], mysql.JSON)
        assert isinstance(db_by_name["severity"]["type"], mysql.VARCHAR)
        assert db_by_name["severity"]["type"].length == 16
        assert isinstance(db_by_name["review_status"]["type"], mysql.VARCHAR)
        assert db_by_name["review_status"]["type"].length == 24
        for name in ("occurred_at", "received_at", "reviewed_at"):
            assert isinstance(db_by_name[name]["type"], mysql.DATETIME)
            assert db_by_name[name]["type"].fsp == 3
        assert all(
            db_by_name[name]["nullable"] is table.c[name].nullable
            for name in table.c.keys()
        )
        assert all(db_by_name[name]["default"] is None for name in table.c.keys())
        assert inspector.get_pk_constraint("assessment_event")["constrained_columns"] == ["id"]
        unique_constraints = inspector.get_unique_constraints("assessment_event")
        assert {
            (item["name"], tuple(item["column_names"]))
            for item in unique_constraints
        } == {
            ("uk_attempt_client_event", ("tenant_id", "attempt_id", "client_event_id"))
        }
        db_indexes = inspector.get_indexes("assessment_event")
        assert {item["name"] for item in db_indexes} == {
            "uk_attempt_client_event",
            "idx_attempt_event_received",
        }
        assert any(
            item["name"] == "idx_attempt_event_received"
            and item["column_names"] == ["attempt_id", "received_at", "event_type"]
            and item["unique"] is False
            for item in db_indexes
        )
        assert inspector.get_foreign_keys("assessment_event") == []
    finally:
        engine.dispose()


@requires_db
def test_assessment_attempt_database_matches_metadata() -> None:
    from sqlalchemy import create_engine, inspect

    from app.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        inspector = inspect(engine)
        assert inspector.has_table("assessment_attempt")
        table = Base.metadata.tables["assessment_attempt"]
        db_columns = inspector.get_columns("assessment_attempt")
        assert {column["name"] for column in db_columns} == {column.name for column in table.c}
        db_by_name = {column["name"]: column for column in db_columns}
        for name in ("id", "tenant_id", "task_id", "talent_id", "user_id"):
            assert isinstance(db_by_name[name]["type"], mysql.BIGINT)
            assert db_by_name[name]["type"].unsigned is True
        assert isinstance(db_by_name["attempt_no"]["type"], mysql.TINYINT)
        assert db_by_name["attempt_no"]["type"].unsigned is True
        assert isinstance(db_by_name["answer_version"]["type"], mysql.INTEGER)
        assert db_by_name["answer_version"]["type"].unsigned is True
        assert isinstance(db_by_name["duration_min_snapshot"]["type"], mysql.SMALLINT)
        assert db_by_name["duration_min_snapshot"]["type"].unsigned is True
        assert isinstance(db_by_name["paper_snapshot"]["type"], mysql.JSON)
        assert isinstance(db_by_name["status"]["type"], mysql.VARCHAR)
        assert db_by_name["status"]["type"].length == 24
        assert isinstance(db_by_name["client_device_hash"]["type"], mysql.CHAR)
        assert db_by_name["client_device_hash"]["type"].length == 64
        for name in ("started_at", "deadline_at_snapshot", "last_saved_at", "submitted_at"):
            assert isinstance(db_by_name[name]["type"], mysql.DATETIME)
            assert db_by_name[name]["type"].fsp == 3
        assert all(
            db_by_name[name]["nullable"] is table.c[name].nullable
            for name in table.c.keys()
        )
        assert db_by_name["answer_version"]["default"] in {"0", "'0'"}
        assert inspector.get_pk_constraint("assessment_attempt")["constrained_columns"] == ["id"]
        unique_constraints = inspector.get_unique_constraints("assessment_attempt")
        assert {item["name"] for item in unique_constraints} == {"uk_attempt_task_user_no"}
        assert any(
            item["name"] == "uk_attempt_task_user_no"
            and item["column_names"] == ["task_id", "user_id", "attempt_no"]
            for item in unique_constraints
        )
        db_indexes = inspector.get_indexes("assessment_attempt")
        assert {item["name"] for item in db_indexes} == {
            "idx_attempt_task_status_saved",
            "uk_attempt_task_user_no",
        }
        assert any(
            item["name"] == "idx_attempt_task_status_saved"
            and item["column_names"] == ["task_id", "status", "last_saved_at"]
            and item["unique"] is False
            for item in db_indexes
        )
        assert inspector.get_foreign_keys("assessment_attempt") == []
    finally:
        engine.dispose()


@requires_db
def test_assessment_paper_question_database_matches_metadata() -> None:
    from sqlalchemy import create_engine, inspect

    from app.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        inspector = inspect(engine)
        assert inspector.has_table("assessment_paper_question")
        table = Base.metadata.tables["assessment_paper_question"]
        db_columns = inspector.get_columns("assessment_paper_question")
        assert {column["name"] for column in db_columns} == {column.name for column in table.c}
        db_by_name = {column["name"]: column for column in db_columns}
        for name in ("id", "paper_id", "question_id"):
            assert isinstance(db_by_name[name]["type"], mysql.BIGINT)
            assert db_by_name[name]["type"].unsigned is True
        assert isinstance(db_by_name["question_version"]["type"], mysql.INTEGER)
        assert db_by_name["question_version"]["type"].unsigned is True
        assert isinstance(db_by_name["seq_no"]["type"], mysql.SMALLINT)
        assert db_by_name["seq_no"]["type"].unsigned is True
        assert isinstance(db_by_name["score"]["type"], mysql.DECIMAL)
        assert (db_by_name["score"]["type"].precision, db_by_name["score"]["type"].scale) == (8, 2)
        for name in ("question_snapshot", "dimension_snapshot", "answer_rule_snapshot"):
            assert isinstance(db_by_name[name]["type"], mysql.JSON)
        assert all(column["nullable"] is False for column in db_columns)
        assert inspector.get_pk_constraint("assessment_paper_question")["constrained_columns"] == ["id"]
        assert {item["name"] for item in inspector.get_unique_constraints("assessment_paper_question")} >= {
            "uk_paper_question_seq",
            "uk_paper_question_version",
        }
        assert any(
            index["name"] != "uk_paper_question_seq"
            and index["column_names"] == ["paper_id", "seq_no"]
            and index["unique"] is False
            for index in inspector.get_indexes("assessment_paper_question")
        )
        assert inspector.get_foreign_keys("assessment_paper_question") == []
    finally:
        engine.dispose()
