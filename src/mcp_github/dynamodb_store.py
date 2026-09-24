#!/usr/bin/env python3

# /*
#  * Copyright Said Sef
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      https://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
#  */


"""DynamoDB token store on boto3, called from a worker thread.

The store's interface is async, but OAuth state is a handful of calls per sign-in, so a
thread hop per call costs nothing that matters and the reference client stays current.
Table schema and item shape match the py-key-value-aio store, so an existing table
carries over unchanged."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, override

import boto3
from key_value.aio._utils.managed_entry import ManagedEntry
from key_value.aio.stores.base import BaseStore

TABLE_SCHEMA: dict[str, Any] = {
    "KeySchema": [
        {"AttributeName": "collection", "KeyType": "HASH"},
        {"AttributeName": "key", "KeyType": "RANGE"},
    ],
    "AttributeDefinitions": [
        {"AttributeName": "collection", "AttributeType": "S"},
        {"AttributeName": "key", "AttributeType": "S"},
    ],
    "BillingMode": "PAY_PER_REQUEST",
}

TTL_ATTRIBUTE = "ttl"


class DynamoDBStore(BaseStore):
    """One DynamoDB table keyed by collection and key, with a TTL attribute DynamoDB expires."""

    def __init__(self, *, table_name: str, region_name: str) -> None:
        self._table_name = table_name
        self._region_name = region_name
        self._client: Any = None
        super().__init__(stable_api=True)

    def _item_key(self, collection: str, key: str) -> dict[str, Any]:
        return {"collection": {"S": collection}, "key": {"S": key}}

    def _ensure_table(self) -> None:
        client = self._client
        try:
            client.describe_table(TableName=self._table_name)
        except client.exceptions.ResourceNotFoundException:
            client.create_table(TableName=self._table_name, **TABLE_SCHEMA)
            client.get_waiter("table_exists").wait(TableName=self._table_name)
        ttl = client.describe_time_to_live(TableName=self._table_name)
        if ttl.get("TimeToLiveDescription", {}).get("TimeToLiveStatus") == "DISABLED":
            client.update_time_to_live(
                TableName=self._table_name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
            )

    @override
    async def _setup(self) -> None:
        if self._client is None:
            self._client = await asyncio.to_thread(boto3.client, "dynamodb", region_name=self._region_name)
        await asyncio.to_thread(self._ensure_table)

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await asyncio.to_thread(client.close)

    @override
    async def _get_managed_entry(self, *, key: str, collection: str) -> ManagedEntry | None:
        response = await asyncio.to_thread(
            self._client.get_item, TableName=self._table_name, Key=self._item_key(collection, key)
        )
        item = response.get("Item") or {}
        json_value = item.get("value", {}).get("S")
        if not json_value:
            return None
        entry: ManagedEntry = self._serialization_adapter.load_json(json_str=json_value)
        expires_at = item.get(TTL_ATTRIBUTE, {}).get("N")
        if expires_at:
            entry.expires_at = datetime.fromtimestamp(int(expires_at), tz=UTC)
        return entry

    @override
    async def _put_managed_entry(self, *, key: str, collection: str, managed_entry: ManagedEntry) -> None:
        json_value = self._serialization_adapter.dump_json(entry=managed_entry, key=key, collection=collection)
        item: dict[str, Any] = {**self._item_key(collection, key), "value": {"S": json_value}}
        if managed_entry.expires_at is not None:
            item[TTL_ATTRIBUTE] = {"N": str(int(managed_entry.expires_at.timestamp()))}
        await asyncio.to_thread(self._client.put_item, TableName=self._table_name, Item=item)

    @override
    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        response = await asyncio.to_thread(
            self._client.delete_item,
            TableName=self._table_name,
            Key=self._item_key(collection, key),
            ReturnValues="ALL_OLD",
        )
        return response.get("Attributes") is not None
