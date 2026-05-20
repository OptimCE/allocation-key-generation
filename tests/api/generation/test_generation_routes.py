"""
Integration tests for api/generation/routes.py.

These tests run the full ASGI stack against a real Postgres. NATS is mocked
at the import location (`api.generation.service.send_event` /
`api.generation.service.get_jetstream`) so no broker is required.

Auth follows the same pattern as test_subscription_routes.py: send the
x-user-id / x-community-id / x-user-role headers KrakenD would forward,
and the GatewayScopeMiddleware turns them into ContextVars.

Every test must also create an active `algorithm` subscription, otherwise
the router-level `require_feature(FeatureName.ALGORITHM)` returns 403.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from core.database.models import Community
from shared.const import GenerationStatus
from shared.models.crm_models import (
    AllocationKeyModel,
    ConsumerModel,
    IterationModel,
)
from shared.models.local_models import (
    AllocationKeyGeneratedModel,
    ConsumerGeneratedModel,
    GenerationModel,
    IterationGeneratedModel,
)
from tests.factories.generation_factory import (
    create_full_key_tree,
    create_generation,
)
from tests.factories.subscription_factory import (
    create_community,
    create_subscription,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_headers(community: Community) -> dict[str, str]:
    return {
        "x-user-id": "test|admin",
        "x-community-id": community.auth_community_id,
        "x-user-role": "ADMIN",
    }


# Helpers for the multipart POST /generation/ shape. The route accepts the
# file as ``files["file"]`` and the metadata (including ``inputs`` as a JSON
# string) as ``data``. Tests mock ``api.generation.service.storage.upload``
# so no real MinIO is contacted.
_DEFAULT_FILE = ("data.csv", b"col1,col2,production\n1,2,3\n", "text/csv")


def _multipart_payload(
    *,
    name: str = "my generation",
    injection_name: str = "production",
    algorithm_name: str = "brute_force",
    inputs: dict | None = None,
    file: tuple[str, bytes, str] | None = None,
) -> dict:
    """Build the kwargs for ``client.post`` to hit the multipart endpoint."""
    return {
        "files": {"file": file or _DEFAULT_FILE},
        "data": {
            "name": name,
            "injection_name": injection_name,
            "algorithm_name": algorithm_name,
            "inputs": json.dumps(inputs if inputs is not None else {"iterations": 2}),
        },
    }


async def _community_with_subscription(db_session) -> Community:
    """Generation routes are gated by `require_feature(ALGORITHM)`.

    Every test that hits a generation route needs both the community row
    (so `resolve_internal_community` can resolve the auth id) and an active
    subscription (so `require_feature` lets the request through).
    """
    community = await create_community(db_session)
    await create_subscription(db_session, id_community=community.id, is_active=True)
    return community


# ---------------------------------------------------------------------------
# GET /generation/algorithms
# ---------------------------------------------------------------------------


async def test_get_algorithms_returns_brute_force_and_olagsa(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.get("/algorithms", headers=_admin_headers(community))

    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == 0
    names = {algo["name"] for algo in body["data"]}
    assert "brute_force" in names
    assert "olagsa" in names


# ---------------------------------------------------------------------------
# GET /generation/algorithms/{algorithm_name}
# ---------------------------------------------------------------------------


async def test_get_algorithm_inputs_brute_force_returns_input_schema(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.get("/algorithms/brute_force", headers=_admin_headers(community))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "brute_force"
    assert data["queue"] == "optimce.allocation.brute_force"
    schema = data["input_schema"]
    assert "iterations" in schema["properties"]


async def test_get_algorithm_inputs_olagsa_returns_input_schema(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.get("/algorithms/olagsa", headers=_admin_headers(community))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "olagsa"
    properties = data["input_schema"]["properties"]
    assert "iterations" in properties
    assert "population_size" in properties


async def test_get_algorithm_inputs_unknown_returns_404_with_2010(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.get(
        "/algorithms/does_not_exist",
        headers=_admin_headers(community),
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == 2010  # ALGORITHM_NOT_FOUND


# ---------------------------------------------------------------------------
# GET /generation/  (list)
# ---------------------------------------------------------------------------


async def test_get_generations_empty_returns_pagination_zero(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.get("/", headers=_admin_headers(community))

    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == 0
    assert body["data"] == []
    assert body["pagination"]["total"] == 0


async def test_get_generations_returns_only_current_community(client, db_session):
    community_a = await _community_with_subscription(db_session)
    community_b = await _community_with_subscription(db_session)
    await create_generation(db_session, id_community=community_a.id, name="A row")
    await create_generation(db_session, id_community=community_b.id, name="B row")

    response = await client.get("/", headers=_admin_headers(community_a))

    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert len(body["data"]) == 1
    assert body["data"][0]["name"] == "A row"


async def test_get_generations_filter_by_status(client, db_session):
    community = await _community_with_subscription(db_session)
    await create_generation(db_session, id_community=community.id, status=GenerationStatus.PENDING)
    await create_generation(db_session, id_community=community.id, status=GenerationStatus.PENDING)
    success = await create_generation(
        db_session,
        id_community=community.id,
        status=GenerationStatus.SUCCESS,
        name="successful",
    )

    response = await client.get("/?status=1", headers=_admin_headers(community))

    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["data"][0]["id"] == success.id
    assert body["data"][0]["status"] == int(GenerationStatus.SUCCESS)


async def test_get_generations_pagination(client, db_session):
    community = await _community_with_subscription(db_session)
    for _ in range(3):
        await create_generation(db_session, id_community=community.id)

    response = await client.get("/?page=1&page_size=2", headers=_admin_headers(community))

    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 3
    assert body["pagination"]["total_pages"] == 2
    assert len(body["data"]) == 2


# ---------------------------------------------------------------------------
# POST /generation/  (start) — NATS + storage mocked
#
# The route is multipart/form-data: the file is uploaded alongside the
# metadata, the service uploads it to MinIO and stores the object key on
# the row, and the worker is responsible for deleting it. All POST tests
# mock ``storage.upload`` so no real MinIO is needed; the assertion
# pattern is "uploader was awaited with a key matching the persisted row".
# ---------------------------------------------------------------------------


@patch("api.generation.service.storage.delete", new_callable=AsyncMock)
@patch("api.generation.service.storage.upload", new_callable=AsyncMock)
@patch("api.generation.service.get_jetstream", return_value=MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_generation_brute_force_publishes_event_and_returns_pending(
    mock_send, mock_jetstream, mock_upload, mock_delete, client, db_session
):
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        **_multipart_payload(name="my generation", inputs={"iterations": 2}),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == 0
    assert body["data"]["status"] == int(GenerationStatus.PENDING)
    new_id = body["data"]["id"]

    # The file was uploaded to MinIO exactly once before publishing.
    mock_upload.assert_awaited_once()
    uploaded_key = mock_upload.await_args.args[0]
    uploaded_content = mock_upload.await_args.args[1]
    assert uploaded_key.startswith(f"allocations/{community.id}/")
    assert uploaded_key.endswith("/data.csv")
    assert uploaded_content == _DEFAULT_FILE[1]

    # The NATS publish was performed once on the algorithm's queue.
    mock_send.assert_awaited_once()
    args, _kwargs = mock_send.call_args
    assert args[1] == "optimce.allocation.brute_force"
    event = args[2]
    assert event.type == "generation.requested"
    assert event.data == {"generation_id": new_id}

    # The row was persisted with the uploaded key.
    row = (
        await db_session.execute(select(GenerationModel).where(GenerationModel.id == new_id))
    ).scalar_one()
    assert row.status == GenerationStatus.PENDING
    assert row.algorithm_name == "brute_force"
    assert row.algorithm_version == "1.0"
    assert row.inputs == {"iterations": 2}
    assert row.id_community == community.id
    assert row.file_storage_key == uploaded_key
    assert row.file_name == "data.csv"

    # Happy path: no rollback, so service.storage.delete is never called.
    mock_delete.assert_not_awaited()


@patch("api.generation.service.storage.delete", new_callable=AsyncMock)
@patch("api.generation.service.storage.upload", new_callable=AsyncMock)
@patch("api.generation.service.get_jetstream", return_value=MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_generation_then_list_returns_the_new_row(
    mock_send, mock_jetstream, mock_upload, mock_delete, client, db_session
):
    community = await _community_with_subscription(db_session)

    create = await client.post(
        "/",
        headers=_admin_headers(community),
        **_multipart_payload(name="round trip", inputs={"iterations": 1}),
    )
    assert create.status_code == 200
    new_id = create.json()["data"]["id"]

    listing = await client.get("/", headers=_admin_headers(community))

    assert listing.status_code == 200
    ids = [row["id"] for row in listing.json()["data"]]
    assert new_id in ids


@patch("api.generation.service.storage.delete", new_callable=AsyncMock)
@patch("api.generation.service.storage.upload", new_callable=AsyncMock)
@patch("api.generation.service.get_jetstream", return_value=MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_generation_olagsa_publishes_to_olagsa_queue(
    mock_send, mock_jetstream, mock_upload, mock_delete, client, db_session
):
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        **_multipart_payload(name="olagsa run", algorithm_name="olagsa", inputs={"iterations": 1}),
    )

    assert response.status_code == 200
    mock_upload.assert_awaited_once()
    mock_send.assert_awaited_once()
    assert mock_send.call_args[0][1] == "optimce.allocation.olagsa"


@patch("api.generation.service.storage.delete", new_callable=AsyncMock)
@patch("api.generation.service.storage.upload", new_callable=AsyncMock)
@patch("api.generation.service.get_jetstream", return_value=MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_generation_unknown_algorithm_returns_404_with_2010(
    mock_send, mock_jetstream, mock_upload, mock_delete, client, db_session
):
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        **_multipart_payload(algorithm_name="missing", inputs={}),
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == 2010
    mock_send.assert_not_awaited()
    # Validation happens before upload, so MinIO is never touched.
    mock_upload.assert_not_awaited()


@patch("api.generation.service.storage.delete", new_callable=AsyncMock)
@patch("api.generation.service.storage.upload", new_callable=AsyncMock)
@patch("api.generation.service.get_jetstream", return_value=MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_generation_invalid_inputs_returns_422_with_2012(
    mock_send, mock_jetstream, mock_upload, mock_delete, client, db_session
):
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        **_multipart_payload(inputs={"iterations": 99}),  # ge=1, le=3
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == 2012
    mock_send.assert_not_awaited()
    mock_upload.assert_not_awaited()


async def test_start_generation_malformed_inputs_json_returns_422(client, db_session):
    """A non-JSON or non-object ``inputs`` form field is a 422 with the
    INVALID_ALGORITHM_INPUTS code, surfaced by the route before any
    service work happens.
    """
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        files={"file": _DEFAULT_FILE},
        data={
            "name": "bad inputs",
            "injection_name": "production",
            "algorithm_name": "brute_force",
            "inputs": "this is not json",
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == 2012


@patch(
    "api.generation.service.GenerationService._mark_failed_to_queue",
    new_callable=AsyncMock,
)
@patch("api.generation.service.storage.delete", new_callable=AsyncMock)
@patch("api.generation.service.storage.upload", new_callable=AsyncMock)
@patch("api.generation.service.get_jetstream", return_value=MagicMock())
@patch(
    "api.generation.service.send_event",
    new_callable=AsyncMock,
    side_effect=RuntimeError("nats down"),
)
async def test_start_generation_publish_failure_returns_500_and_marks_failed(
    mock_send,
    mock_jetstream,
    mock_upload,
    mock_delete,
    mock_mark_failed,
    client,
    db_session,
):
    """When the NATS publish fails after the row was committed, the route
    must return 500 with `START_GENERATION`, the cleanup helper must be
    invoked with the freshly created generation id, AND the uploaded MinIO
    object must be deleted to avoid leaking orphans.

    `_mark_failed_to_queue` is patched because it opens a fresh
    `AsyncSessionLocalFactory` session that would bypass the per-test
    rollback transaction.
    """
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        **_multipart_payload(inputs={"iterations": 1}),
    )

    assert response.status_code == 500
    assert response.json()["error_code"] == 2011  # START_GENERATION

    mock_send.assert_awaited_once()
    mock_mark_failed.assert_awaited_once()
    failed_id, reason = mock_mark_failed.call_args[0]
    assert isinstance(failed_id, int)
    assert "nats down" in reason

    # Upload happened; rollback delete must have used the same key.
    mock_upload.assert_awaited_once()
    uploaded_key = mock_upload.await_args.args[0]
    mock_delete.assert_awaited_once_with(uploaded_key)


@patch(
    "api.generation.service.storage.upload",
    new_callable=AsyncMock,
    side_effect=RuntimeError("minio down"),
)
async def test_start_generation_storage_upload_failure_returns_502(mock_upload, client, db_session):
    """Upload happens before the DB row is written, so a storage failure
    surfaces as 502 STORAGE_UPLOAD_FAILED and leaves no generation row
    behind.
    """
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        **_multipart_payload(name="upload fails", inputs={"iterations": 1}),
    )

    assert response.status_code == 502
    assert response.json()["error_code"] == 2013  # STORAGE_UPLOAD_FAILED
    mock_upload.assert_awaited_once()

    rows = (
        (
            await db_session.execute(
                select(GenerationModel).where(GenerationModel.name == "upload fails")
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


@patch("api.generation.service.storage.delete", new_callable=AsyncMock)
@patch("api.generation.service.storage.upload", new_callable=AsyncMock)
async def test_start_generation_empty_file_returns_422_and_skips_upload(
    mock_upload, mock_delete, client, db_session
):
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/",
        headers=_admin_headers(community),
        files={"file": ("empty.csv", b"", "text/csv")},
        data={
            "name": "empty file",
            "injection_name": "production",
            "algorithm_name": "brute_force",
            "inputs": json.dumps({"iterations": 1}),
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == 2014  # INVALID_FILE
    mock_upload.assert_not_awaited()
    mock_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /generation/{id}  (allocation keys list)
# ---------------------------------------------------------------------------


async def test_get_allocation_keys_for_generation_returns_partials(client, db_session):
    community = await _community_with_subscription(db_session)
    generation = await create_generation(db_session, id_community=community.id)
    await create_full_key_tree(
        db_session,
        id_community=community.id,
        id_generation=generation.id,
        iterations=2,
        consumers_per_iteration=2,
        iteration_surplus=1.5,
        name="Key 1",
    )
    await create_full_key_tree(
        db_session,
        id_community=community.id,
        id_generation=generation.id,
        iterations=1,
        consumers_per_iteration=1,
        iteration_surplus=2.0,
        name="Key 2",
    )

    response = await client.get(f"/{generation.id}", headers=_admin_headers(community))

    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == 0
    assert body["pagination"]["total"] == 2
    names = {k["name"] for k in body["data"]}
    assert names == {"Key 1", "Key 2"}
    # surplus_total is the sum of the iterations' surplus_total values
    surplus_by_name = {k["name"]: k["surplus_total"] for k in body["data"]}
    assert surplus_by_name["Key 1"] == 3.0  # 2 iterations * 1.5
    assert surplus_by_name["Key 2"] == 2.0  # 1 iteration * 2.0


async def test_get_allocation_keys_for_other_community_returns_empty(client, db_session):
    community_a = await _community_with_subscription(db_session)
    community_b = await _community_with_subscription(db_session)
    generation = await create_generation(db_session, id_community=community_b.id)
    await create_full_key_tree(
        db_session,
        id_community=community_b.id,
        id_generation=generation.id,
    )

    response = await client.get(f"/{generation.id}", headers=_admin_headers(community_a))

    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /generation/key/{id_key}
# ---------------------------------------------------------------------------


async def test_get_allocation_key_returns_full_tree(client, db_session):
    community = await _community_with_subscription(db_session)
    key = await create_full_key_tree(
        db_session,
        id_community=community.id,
        iterations=2,
        consumers_per_iteration=3,
        iteration_surplus=1.0,
    )

    response = await client.get(f"/key/{key.id}", headers=_admin_headers(community))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == key.id
    assert data["surplus_total"] == 2.0  # 2 iterations * 1.0
    assert len(data["iterations"]) == 2
    for iteration in data["iterations"]:
        assert len(iteration["consumers"]) == 3


async def test_get_allocation_key_unknown_returns_400_with_2003(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.get("/key/999999", headers=_admin_headers(community))

    assert response.status_code == 400
    assert response.json()["error_code"] == 2003  # ALLOCATION_KEY_NOT_FOUND


async def test_get_allocation_key_for_other_community_returns_400(client, db_session):
    community_a = await _community_with_subscription(db_session)
    community_b = await _community_with_subscription(db_session)
    key = await create_full_key_tree(db_session, id_community=community_b.id)

    response = await client.get(f"/key/{key.id}", headers=_admin_headers(community_a))

    assert response.status_code == 400
    assert response.json()["error_code"] == 2003


# ---------------------------------------------------------------------------
# POST /generation/save
# ---------------------------------------------------------------------------


async def test_save_key_copies_local_key_into_crm_and_returns_success(client, db_session):
    community = await _community_with_subscription(db_session)
    key = await create_full_key_tree(
        db_session,
        id_community=community.id,
        iterations=2,
        consumers_per_iteration=2,
        iteration_surplus=1.0,
        name="to-save",
        description="a key",
    )

    response = await client.post(
        "/save",
        headers=_admin_headers(community),
        json={"id_key": key.id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == 0
    assert body["data"] == "success"

    # Verify the CRM rows were created with the right shape.
    crm_keys = (
        (
            await db_session.execute(
                select(AllocationKeyModel).where(AllocationKeyModel.name == "to-save")
            )
        )
        .scalars()
        .all()
    )
    assert len(crm_keys) == 1
    crm_key = crm_keys[0]
    assert crm_key.id_community == community.id

    crm_iterations = (
        (
            await db_session.execute(
                select(IterationModel).where(IterationModel.id_key == crm_key.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(crm_iterations) == 2

    crm_consumers = (
        (
            await db_session.execute(
                select(ConsumerModel).where(
                    ConsumerModel.id_iteration.in_([i.id for i in crm_iterations])
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(crm_consumers) == 4


async def test_save_key_unknown_returns_400_with_allocation_key_not_found(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.post(
        "/save",
        headers=_admin_headers(community),
        json={"id_key": 999999},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == 2003  # ALLOCATION_KEY_NOT_FOUND


# ---------------------------------------------------------------------------
# DELETE /generation/generation/{id_generation}
# ---------------------------------------------------------------------------


async def test_delete_generation_removes_row(client, db_session):
    community = await _community_with_subscription(db_session)
    generation = await create_generation(db_session, id_community=community.id)

    response = await client.delete(
        f"/generation/{generation.id}",
        headers=_admin_headers(community),
    )

    assert response.status_code == 200
    assert response.json()["data"] == "success"

    remaining = (
        await db_session.execute(select(GenerationModel).where(GenerationModel.id == generation.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_delete_generation_unknown_returns_400_with_2007(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.delete("/generation/999999", headers=_admin_headers(community))

    assert response.status_code == 400
    assert response.json()["error_code"] == 2007  # GENERATION_NOT_FOUND


async def test_delete_generation_cascades_to_keys_iterations_consumers(client, db_session):
    community = await _community_with_subscription(db_session)
    generation = await create_generation(db_session, id_community=community.id)
    key = await create_full_key_tree(
        db_session,
        id_community=community.id,
        id_generation=generation.id,
        iterations=2,
        consumers_per_iteration=2,
    )
    iteration_ids = key.iteration_ids

    response = await client.delete(
        f"/generation/{generation.id}",
        headers=_admin_headers(community),
    )

    assert response.status_code == 200

    keys = (
        await db_session.execute(
            select(AllocationKeyGeneratedModel).where(AllocationKeyGeneratedModel.id == key.id)
        )
    ).scalar_one_or_none()
    assert keys is None

    iterations = (
        (
            await db_session.execute(
                select(IterationGeneratedModel).where(IterationGeneratedModel.id.in_(iteration_ids))
            )
        )
        .scalars()
        .all()
    )
    assert iterations == []

    consumers = (
        (
            await db_session.execute(
                select(ConsumerGeneratedModel).where(
                    ConsumerGeneratedModel.id_iteration.in_(iteration_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    assert consumers == []


# ---------------------------------------------------------------------------
# DELETE /generation/key/{id_key}
# ---------------------------------------------------------------------------


async def test_delete_key_removes_row_and_cascades(client, db_session):
    community = await _community_with_subscription(db_session)
    key = await create_full_key_tree(
        db_session,
        id_community=community.id,
        iterations=1,
        consumers_per_iteration=2,
    )
    iteration_ids = key.iteration_ids

    response = await client.delete(f"/key/{key.id}", headers=_admin_headers(community))

    assert response.status_code == 200
    assert response.json()["data"] == "success"

    remaining = (
        await db_session.execute(
            select(AllocationKeyGeneratedModel).where(AllocationKeyGeneratedModel.id == key.id)
        )
    ).scalar_one_or_none()
    assert remaining is None

    iterations = (
        (
            await db_session.execute(
                select(IterationGeneratedModel).where(IterationGeneratedModel.id.in_(iteration_ids))
            )
        )
        .scalars()
        .all()
    )
    assert iterations == []


async def test_delete_key_unknown_returns_400_with_allocation_key_not_found(client, db_session):
    community = await _community_with_subscription(db_session)

    response = await client.delete("/key/999999", headers=_admin_headers(community))

    assert response.status_code == 400
    assert response.json()["error_code"] == 2003


# ---------------------------------------------------------------------------
# Auth / feature gating
# ---------------------------------------------------------------------------


async def test_get_generations_without_subscription_returns_403(client, db_session):
    community = await create_community(db_session)  # no subscription

    response = await client.get("/", headers=_admin_headers(community))

    assert response.status_code == 403
    assert response.json()["error_code"] == 1003  # NOT_SUBSCRIBED


# ---------------------------------------------------------------------------
# Multi-tenant isolation — DELETE / save across two communities
#
# Read routes already have isolation tests (see test_get_generations_returns_
# only_current_community, test_get_allocation_keys_for_other_community_returns_
# empty, test_get_allocation_key_for_other_community_returns_400). The audit
# in PRODUCTION_ROADMAP.md called out the DELETE/save routes as the spot-
# check gap; these tests close it by proving that:
#   1. Cross-community DELETE returns the "not found" error and leaves the
#      target row intact (no silent cross-tenant deletion).
#   2. Cross-community save returns "not found" and writes nothing to CRM.
# ---------------------------------------------------------------------------


async def test_delete_generation_for_other_community_returns_400_and_preserves_row(
    client, db_session
):
    community_a = await _community_with_subscription(db_session)
    community_b = await _community_with_subscription(db_session)
    generation_b = await create_generation(
        db_session, id_community=community_b.id, name="B's generation"
    )

    response = await client.delete(
        f"/generation/{generation_b.id}",
        headers=_admin_headers(community_a),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == 2007  # GENERATION_NOT_FOUND

    # B's generation must still exist — A had no business deleting it.
    surviving = (
        await db_session.execute(
            select(GenerationModel).where(GenerationModel.id == generation_b.id)
        )
    ).scalar_one_or_none()
    assert surviving is not None
    assert surviving.id_community == community_b.id


async def test_delete_key_for_other_community_returns_400_and_preserves_row(client, db_session):
    community_a = await _community_with_subscription(db_session)
    community_b = await _community_with_subscription(db_session)
    key_b = await create_full_key_tree(
        db_session,
        id_community=community_b.id,
        iterations=1,
        consumers_per_iteration=1,
    )

    response = await client.delete(
        f"/key/{key_b.id}",
        headers=_admin_headers(community_a),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == 2003  # ALLOCATION_KEY_NOT_FOUND

    surviving = (
        await db_session.execute(
            select(AllocationKeyGeneratedModel).where(AllocationKeyGeneratedModel.id == key_b.id)
        )
    ).scalar_one_or_none()
    assert surviving is not None
    assert surviving.id_community == community_b.id


async def test_save_key_for_other_community_returns_400_and_does_not_write_crm(client, db_session):
    community_a = await _community_with_subscription(db_session)
    community_b = await _community_with_subscription(db_session)
    key_b = await create_full_key_tree(
        db_session,
        id_community=community_b.id,
        iterations=1,
        consumers_per_iteration=1,
        name="B's saved-key candidate",
    )

    response = await client.post(
        "/save",
        headers=_admin_headers(community_a),
        json={"id_key": key_b.id},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == 2003  # ALLOCATION_KEY_NOT_FOUND

    # No CRM rows created for either community — the save was rejected
    # before any cross-DB write could happen.
    crm_rows = (
        (
            await db_session.execute(
                select(AllocationKeyModel).where(
                    AllocationKeyModel.name == "B's saved-key candidate"
                )
            )
        )
        .scalars()
        .all()
    )
    assert crm_rows == []
