"""The allocation-key-generation part of a data export (api/data_export.py).

Through the kit's own contract check (`run_contract_check`): the answer matches the contract,
every file listed was written, and each JSON file is checked here against its item schema
(core/export/schemas/files/allocation-key-generation/).
"""

from __future__ import annotations

import json
import pathlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.data_export import TABLES, export_handler
from core.export import ExportHandler, run_contract_check
from tests.factories.generation_factory import create_full_key_tree, create_generation
from tests.factories.subscription_factory import create_community, create_subscription

pytestmark = pytest.mark.asyncio

SERVICE = "allocation-key-generation"
SCHEMAS = pathlib.Path(__file__).resolve().parent.parent / f"core/export/schemas/files/{SERVICE}"
BUCKET = "crm-files"


def handler_for(session: AsyncSession, objects: dict[str, int]) -> ExportHandler:
    @asynccontextmanager
    async def shared() -> AsyncIterator[AsyncSession]:
        yield session

    async def size_of(key: str) -> int | None:
        return objects.get(key)

    return export_handler(local=shared, crm=shared, size_of=size_of, bucket=BUCKET)


def _validators() -> dict[str, Draft202012Validator]:
    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in SCHEMAS.glob("*.schema.json")]
    registry: Registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    return {
        schema["title"].split("/")[1].removesuffix(".json item"): Draft202012Validator(
            schema, registry=registry, format_checker=FormatChecker()
        )
        for schema in schemas
        if schema.get("title", "").endswith(" item")
    }


async def export(
    session: AsyncSession,
    *,
    scope: dict[str, str],
    requester: str,
    objects: dict[str, int] | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Runs one part; returns part.done and each JSON file's items, checked against its schema."""
    result = await run_contract_check(
        handler_for(session, objects or {}), service=SERVICE, scope=scope, requester_id=requester
    )
    assert result.outcome == "done", result.message
    validators = _validators()
    files: dict[str, list[dict[str, Any]]] = {}
    for file in result.message["files"]:
        document = json.loads(result.store.text(file["key"]))
        entity = document["entity"]
        errors = [
            f"{entity}[{i}]: {error.message}"
            for i, item in enumerate(document["items"])
            for error in validators[entity].iter_errors(item)
        ]
        assert not errors, errors
        files[entity] = document["items"]
    return result.message, files


def counters(done: dict[str, Any]) -> dict[str, int]:
    prefix = f"export.{SERVICE}."
    return {item["labelKey"].removeprefix(prefix): item["count"] for item in done["summary"]}


async def account(session: AsyncSession, community: Any, sub: str, role: str) -> None:
    user = int(
        (
            await session.execute(
                text("INSERT INTO app_user (auth_user_id, email) VALUES (:s, :e) RETURNING id"),
                {"s": sub, "e": f"{sub}@example.test"},
            )
        ).scalar_one()
    )
    await session.execute(
        text("INSERT INTO community_user (id_community, id_user, role) VALUES (:c, :u, :r)"),
        {"c": community.id, "u": user, "r": role},
    )
    await session.flush()


async def world(session: AsyncSession) -> dict[str, Any]:
    """A community with one finished generation (a key: 2 iterations x 2 consumers), an admin
    and a manager."""
    community = await create_community(session, auth_community_id=f"comm-{uuid.uuid4().hex[:6]}")
    await create_subscription(session, id_community=community.id, feature="algorithm")
    await account(session, community, "user-admin", "ADMIN")
    await account(session, community, "user-manager", "MANAGER")
    generation = await create_generation(
        session,
        id_community=community.id,
        status=1,
        file_storage_key=f"allocations/{community.id}/{uuid.uuid4().hex}/data.csv",
    )
    await create_full_key_tree(session, id_community=community.id, id_generation=generation.id)
    return {"community": community, "generation": generation}


def community_scope(w: dict[str, Any]) -> dict[str, str]:
    return {"type": "community", "id": w["community"].auth_community_id}


class TestCommunityExport:
    async def test_every_table_codes_by_name_files_by_reference(self, db_session):
        w = await world(db_session)
        generation = w["generation"]
        done, files = await export(
            db_session,
            scope=community_scope(w),
            requester="user-admin",
            objects={generation.file_storage_key: 1234},
        )

        assert set(files) == {table.entity for table in TABLES}
        counts = [
            len(files[e])
            for e in (
                "generations",
                "generated-keys",
                "generated-iterations",
                "generated-consumers",
            )
        ]
        assert counts == [1, 1, 2, 4]
        (item,) = files["generations"]
        assert item["status"] == "SUCCESS"
        assert item["inputs"] == {"iterations": 1}
        assert item["algorithm_name"] == "brute_force"
        assert "id_community" not in json.dumps(files)
        assert item["input_file"] == {
            "path": f"{SERVICE}/files/{generation.id}-input-data.csv",
            "file_missing": False,
        }
        assert done["references"] == [
            {
                "bucket": BUCKET,
                "key": generation.file_storage_key,
                "targetPath": f"{SERVICE}/files/{generation.id}-input-data.csv",
                "size": 1234,
            }
        ]
        assert counters(done) == {"generations": 1, "files": 1}

    async def test_a_file_gone_from_storage_is_listed_missing_not_fatal(self, db_session):
        w = await world(db_session)
        done, files = await export(db_session, scope=community_scope(w), requester="user-admin")
        (item,) = files["generations"]
        assert item["input_file"] == {"path": None, "file_missing": True}
        assert done["references"] == []
        assert counters(done)["files_missing"] == 1

    async def test_a_nan_result_is_written_as_null(self, db_session):
        """JSON cannot carry NaN: the file would be unreadable by any strict parser."""
        w = await world(db_session)
        await db_session.execute(
            text(
                "UPDATE allocation_key_generated SET surplus_total = 'NaN' WHERE id_generation = :g"
            ),
            {"g": w["generation"].id},
        )
        _done, files = await export(db_session, scope=community_scope(w), requester="user-admin")
        (key,) = files["generated-keys"]
        assert key["surplus_total"] is None

    async def test_the_module_off_hides_nothing(self, db_session):
        w = await world(db_session)
        await db_session.execute(
            text("UPDATE community_subscription SET is_active = false WHERE id_community = :c"),
            {"c": w["community"].id},
        )
        _done, files = await export(db_session, scope=community_scope(w), requester="user-admin")
        assert len(files["generations"]) == 1

    async def test_only_an_admin_exports_the_community(self, db_session):
        w = await world(db_session)
        result = await run_contract_check(
            handler_for(db_session, {}),
            service=SERVICE,
            scope=community_scope(w),
            requester_id="user-manager",
        )
        assert result.outcome == "failed"
        assert result.message["errorCode"] == "forbidden"

    async def test_an_unknown_community_is_scope_not_found(self, db_session):
        await world(db_session)
        result = await run_contract_check(
            handler_for(db_session, {}),
            service=SERVICE,
            scope={"type": "community", "id": str(uuid.uuid4())},
            requester_id="user-admin",
        )
        assert result.outcome == "failed"
        assert result.message["errorCode"] == "scope_not_found"


class TestUserExport:
    async def test_a_user_export_holds_nothing_of_this_service(self, db_session):
        await world(db_session)
        done, files = await export(
            db_session, scope={"type": "user", "id": "user-admin"}, requester="user-admin"
        )
        assert files == {}
        assert done["files"] == []
        assert done["references"] == []
