"""Tests for dynamodb_store.py - table setup, TTL, the item shape and the client lifecycle."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from key_value.aio.errors import StoreSetupError

from mcp_github.dynamodb_store import TABLE_SCHEMA, TTL_ATTRIBUTE, DynamoDBStore

TABLE = "oauth-state"


class ResourceNotFoundError(Exception):
    """Stands in for the client's generated ResourceNotFoundException."""


def _client(*, table_exists=True, ttl_status="ENABLED"):
    client = MagicMock()
    client.exceptions.ResourceNotFoundException = ResourceNotFoundError
    client.describe_table.side_effect = None if table_exists else ResourceNotFoundError()
    client.describe_time_to_live.return_value = {"TimeToLiveDescription": {"TimeToLiveStatus": ttl_status}}
    client.get_item.return_value = {}
    client.delete_item.return_value = {}
    return client


def _boto3(client):
    module = MagicMock()
    module.client.return_value = client
    return module


def _store():
    return DynamoDBStore(table_name=TABLE, region_name="eu-west-1")


def _run(client, coroutine_factory):
    store = _store()
    with patch("mcp_github.dynamodb_store.boto3", new=_boto3(client)):
        return asyncio.run(coroutine_factory(store))


class TestSetup:
    """What setup does to the table, and how a failure leaves the store."""

    def test_an_existing_table_with_ttl_is_left_alone(self):
        client = _client()
        module = _boto3(client)
        with patch("mcp_github.dynamodb_store.boto3", new=module):
            asyncio.run(_store().setup())
        module.client.assert_called_once_with("dynamodb", region_name="eu-west-1")
        client.describe_table.assert_called_once_with(TableName=TABLE)
        client.create_table.assert_not_called()
        client.update_time_to_live.assert_not_called()

    def test_a_missing_table_is_created_and_waited_for(self):
        client = _client(table_exists=False)
        _run(client, lambda store: store.setup())
        client.create_table.assert_called_once_with(TableName=TABLE, **TABLE_SCHEMA)
        client.get_waiter.assert_called_once_with("table_exists")
        client.get_waiter.return_value.wait.assert_called_once_with(TableName=TABLE)

    def test_disabled_ttl_is_enabled_on_the_ttl_attribute(self):
        client = _client(ttl_status="DISABLED")
        _run(client, lambda store: store.setup())
        client.update_time_to_live.assert_called_once_with(
            TableName=TABLE, TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE}
        )

    def test_a_failure_keeps_the_aws_error_as_the_cause_and_the_retry_reuses_the_client(self):
        client = _client()
        error = ClientError({"Error": {"Code": "ResourceInUseException", "Message": "busy"}}, "CreateTable")
        client.describe_time_to_live.side_effect = [error, {"TimeToLiveDescription": {"TimeToLiveStatus": "ENABLED"}}]
        module = _boto3(client)

        async def run(store):
            with pytest.raises(StoreSetupError) as raised:
                await store.setup()
            assert raised.value.__cause__ is error
            await store.setup()

        with patch("mcp_github.dynamodb_store.boto3", new=module):
            asyncio.run(run(_store()))
        module.client.assert_called_once()

    def test_close_releases_the_client_once(self):
        client = _client()

        async def run(store):
            await store.setup()
            await store.close()
            await store.close()

        _run(client, run)
        client.close.assert_called_once_with()


class TestItems:
    """The item shape written and read, and how DynamoDB's TTL wins."""

    def test_put_writes_the_library_item_shape_with_a_ttl(self):
        client = _client()
        _run(client, lambda store: store.put(key="k", value={"a": 1}, collection="c", ttl=60))
        item = client.put_item.call_args.kwargs["Item"]
        assert client.put_item.call_args.kwargs["TableName"] == TABLE
        assert item["collection"] == {"S": "c"}
        assert item["key"] == {"S": "k"}
        assert "S" in item["value"]
        assert int(item[TTL_ATTRIBUTE]["N"]) > int(datetime.now(tz=UTC).timestamp())

    def test_put_without_a_ttl_writes_no_ttl_attribute(self):
        client = _client()
        _run(client, lambda store: store.put(key="k", value={"a": 1}, collection="c"))
        assert TTL_ATTRIBUTE not in client.put_item.call_args.kwargs["Item"]

    def test_get_reads_back_what_put_wrote(self):
        client = _client()

        async def round_trip(store):
            await store.put(key="k", value={"a": 1}, collection="c", ttl=60)
            client.get_item.return_value = {"Item": client.put_item.call_args.kwargs["Item"]}
            return await store.get(key="k", collection="c")

        assert _run(client, round_trip) == {"a": 1}
        client.get_item.assert_called_with(TableName=TABLE, Key={"collection": {"S": "c"}, "key": {"S": "k"}})

    def test_the_table_ttl_overrides_the_entry_ttl(self):
        client = _client()
        expired = str(int((datetime.now(tz=UTC) - timedelta(hours=1)).timestamp()))

        async def round_trip(store):
            await store.put(key="k", value={"a": 1}, collection="c", ttl=3600)
            item = {**client.put_item.call_args.kwargs["Item"], TTL_ATTRIBUTE: {"N": expired}}
            client.get_item.return_value = {"Item": item}
            return await store.get(key="k", collection="c")

        assert _run(client, round_trip) is None

    @pytest.mark.parametrize("response", [{}, {"Item": {}}, {"Item": {"value": {"S": ""}}}])
    def test_a_missing_or_empty_item_is_none(self, response):
        client = _client()
        client.get_item.return_value = response
        assert _run(client, lambda store: store.get(key="k", collection="c")) is None

    @pytest.mark.parametrize(("response", "deleted"), [({"Attributes": {"key": {"S": "k"}}}, True), ({}, False)])
    def test_delete_reports_whether_an_item_was_there(self, response, deleted):
        client = _client()
        client.delete_item.return_value = response
        assert _run(client, lambda store: store.delete(key="k", collection="c")) is deleted
        client.delete_item.assert_called_once_with(
            TableName=TABLE, Key={"collection": {"S": "c"}, "key": {"S": "k"}}, ReturnValues="ALL_OLD"
        )
