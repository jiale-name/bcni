"""TBL-12/TBL-13 model and DAO contract tests."""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Column, DateTime, Integer, JSON, MetaData, String, Table, Text, create_engine
from sqlalchemy.orm import Session

from app.dao import talent_dao
from app.models.base import Base
from app.models.talent import TalentProjectExp, TalentWorkExp


def _sqlite_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    for table_name, required_name in (
        ("talent_work_exp", "company"),
        ("talent_project_exp", "project_name"),
    ):
        columns = [
            Column("id", BigInteger, primary_key=True),
            Column("tenant_id", BigInteger, nullable=False),
            Column("talent_id", BigInteger, nullable=False),
            Column("seq_no", Integer, nullable=False, default=0),
            Column(required_name, String(128), nullable=False),
            Column("start_date", DateTime, nullable=True),
            Column("end_date", DateTime, nullable=True),
            Column("description", Text, nullable=True),
            Column("source", Integer, nullable=False, default=0),
            Column("is_deleted", Integer, nullable=False, default=0),
            Column("deleted_at", DateTime, nullable=True),
            Column("created_at", DateTime, nullable=True),
            Column("updated_at", DateTime, nullable=True),
        ]
        if table_name == "talent_work_exp":
            columns.extend(
                [
                    Column("dept_name", String(128), nullable=True),
                    Column("position_name", String(128), nullable=True),
                    Column("is_current", Integer, nullable=False, default=0),
                ]
            )
        else:
            columns.extend(
                [
                    Column("role_name", String(64), nullable=True),
                    Column("tech_stack", JSON, nullable=True),
                ]
            )
        Table(table_name, metadata, *columns)
    metadata.create_all(engine)
    return Session(engine)


def test_experience_models_match_the_database_contract() -> None:
    work = TalentWorkExp.__table__
    project = TalentProjectExp.__table__

    assert work.name == "talent_work_exp"
    assert project.name == "talent_project_exp"
    assert work.c.company.nullable is False
    assert project.c.project_name.nullable is False
    assert work.c.start_date.type.__class__.__name__ == "DATE"
    assert project.c.tech_stack.type.__class__.__name__ == "JSON"
    assert {index.name for index in work.indexes} == {"idx_tenant_talent"}
    assert {index.name for index in project.indexes} == {"idx_tenant_talent"}


def test_list_experiences_is_tenant_scoped_soft_delete_aware_and_stable() -> None:
    with _sqlite_session() as db:
        db.add_all(
            [
                TalentWorkExp(
                    id=101,
                    tenant_id=1,
                    talent_id=9001,
                    seq_no=2,
                    company="B",
                    start_date=date(2022, 1, 1),
                ),
                TalentWorkExp(
                    id=100,
                    tenant_id=1,
                    talent_id=9001,
                    seq_no=1,
                    company="A",
                    start_date=date(2020, 1, 1),
                ),
                TalentWorkExp(
                    id=102,
                    tenant_id=2,
                    talent_id=9001,
                    seq_no=1,
                    company="Other tenant",
                ),
                TalentProjectExp(
                    id=201,
                    tenant_id=1,
                    talent_id=9001,
                    seq_no=1,
                    project_name="P1",
                    tech_stack=["Python"],
                ),
            ]
        )
        db.commit()

        assert [row.id for row in talent_dao.list_work_exps(db, 1, 9001)] == [100, 101]
        assert [row.id for row in talent_dao.list_work_exps(db, 2, 9001)] == [102]
        assert [row.id for row in talent_dao.list_project_exps(db, 1, 9001)] == [201]

        assert talent_dao.soft_delete_work_exps(db, 1, 9001) == 2
        db.commit()

        assert talent_dao.list_work_exps(db, 1, 9001) == []
        assert [row.id for row in talent_dao.list_work_exps(db, 2, 9001)] == [102]


def test_replace_experiences_soft_deletes_old_rows_and_assigns_scope() -> None:
    with _sqlite_session() as db:
        old_work = TalentWorkExp(
            id=301,
            tenant_id=7,
            talent_id=7001,
            seq_no=1,
            company="Old",
        )
        old_project = TalentProjectExp(
            id=401,
            tenant_id=7,
            talent_id=7001,
            seq_no=1,
            project_name="Old project",
        )
        db.add_all([old_work, old_project])
        db.commit()

        new_work = TalentWorkExp(id=302, seq_no=1, company="New")
        new_project = TalentProjectExp(id=402, seq_no=1, project_name="New project")

        assert talent_dao.replace_work_exps(db, 7, 7001, [new_work]) == 1
        assert talent_dao.replace_project_exps(db, 7, 7001, [new_project]) == 1
        db.commit()

        assert [(row.id, row.tenant_id, row.talent_id) for row in talent_dao.list_work_exps(db, 7, 7001)] == [
            (302, 7, 7001)
        ]
        assert [row.id for row in talent_dao.list_project_exps(db, 7, 7001)] == [402]
        assert db.get(TalentWorkExp, 301).is_deleted == 1
        assert db.get(TalentProjectExp, 401).is_deleted == 1
