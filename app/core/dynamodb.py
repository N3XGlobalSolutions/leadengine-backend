"""
DynamoDB connection and client utilities for AOB Bot
Single-table design with GSI support
"""

import boto3
from typing import Optional, Dict, Any, List
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from functools import lru_cache

from app.core.config import get_settings

settings = get_settings()


class DynamoDBClient:
    """DynamoDB client manager for single-table design"""

    _resource = None
    _client = None
    _table = None

    @classmethod
    def get_resource(cls):
        """Get DynamoDB resource (singleton)"""
        if cls._resource is None:
            cls._resource = boto3.resource(
                "dynamodb",
                region_name=getattr(settings, "AWS_REGION", "us-east-1"),
                # IAM role credentials auto-detected in Lambda
            )
        return cls._resource

    @classmethod
    def get_client(cls):
        """Get DynamoDB client (for admin operations)"""
        if cls._client is None:
            cls._client = boto3.client(
                "dynamodb",
                region_name=getattr(settings, "AWS_REGION", "us-east-1"),
                # IAM role credentials auto-detected in Lambda
            )
        return cls._client

    @classmethod
    def get_table(cls):
        """Get main application table"""
        if cls._table is None:
            resource = cls.get_resource()
            table_name = getattr(settings, "DYNAMODB_TABLE_NAME", "qbot-prod-backup")
            cls._table = resource.Table(table_name)
        return cls._table


# Convenience functions
def get_dynamodb_table():
    """Get DynamoDB table instance"""
    return DynamoDBClient.get_table()


def get_dynamodb_client():
    """Get DynamoDB client instance"""
    return DynamoDBClient.get_client()


# Helper functions for common operations
def get_item(pk: str, sk: str) -> Optional[Dict[str, Any]]:
    """Get single item by primary key"""
    table = get_dynamodb_table()
    try:
        response = table.get_item(Key={"PK": pk, "SK": sk})
        return response.get("Item")
    except ClientError as e:
        print(f"Error getting item: {e}")
        return None


def put_item(item: Dict[str, Any]) -> bool:
    """Put (create or replace) an item"""
    table = get_dynamodb_table()
    try:
        table.put_item(Item=item)
        return True
    except ClientError as e:
        print(f"Error putting item: {e}")
        return False


def update_item(pk: str, sk: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update item attributes"""
    table = get_dynamodb_table()

    # Build update expression
    update_expr = "SET "
    expr_attr_names = {}
    expr_attr_values = {}

    for i, (key, value) in enumerate(updates.items()):
        attr_name = f"#attr{i}"
        attr_value = f":val{i}"
        update_expr += f"{attr_name} = {attr_value}, "
        expr_attr_names[attr_name] = key
        expr_attr_values[attr_value] = value

    update_expr = update_expr.rstrip(", ")

    try:
        response = table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes")
    except ClientError as e:
        print(f"Error updating item: {e}")
        return None


def delete_item(pk: str, sk: str) -> bool:
    """Delete an item"""
    table = get_dynamodb_table()
    try:
        table.delete_item(Key={"PK": pk, "SK": sk})
        return True
    except ClientError as e:
        print(f"Error deleting item: {e}")
        return False


def query_items(
    pk: str,
    sk_prefix: Optional[str] = None,
    index_name: Optional[str] = None,
    limit: int = 100,
    scan_forward: bool = True,
) -> List[Dict[str, Any]]:
    """Query items by partition key and optional sort key prefix"""
    table = get_dynamodb_table()

    # Build key condition
    if index_name:
        key_condition = Key(f"{index_name}PK").eq(pk)
        if sk_prefix:
            key_condition = key_condition & Key(f"{index_name}SK").begins_with(
                sk_prefix
            )
    else:
        key_condition = Key("PK").eq(pk)
        if sk_prefix:
            key_condition = key_condition & Key("SK").begins_with(sk_prefix)

    try:
        query_params = {
            "KeyConditionExpression": key_condition,
            "Limit": limit,
            "ScanIndexForward": scan_forward,
        }

        # Only add IndexName if it's not None
        if index_name:
            query_params["IndexName"] = index_name

        response = table.query(**query_params)
        return response.get("Items", [])
    except ClientError as e:
        print(f"Error querying items: {e}")
        return []


def count_items(
    pk: str,
    sk_prefix: Optional[str] = None,
    index_name: Optional[str] = None,
) -> int:
    """Efficiently count items by partition key and optional sort key prefix.
    Navigates pagination to return the exact total across all chunks."""
    table = get_dynamodb_table()

    # Build key condition
    if index_name:
        key_condition = Key(f"{index_name}PK").eq(pk)
        if sk_prefix:
            key_condition = key_condition & Key(f"{index_name}SK").begins_with(
                sk_prefix
            )
    else:
        key_condition = Key("PK").eq(pk)
        if sk_prefix:
            key_condition = key_condition & Key("SK").begins_with(sk_prefix)

    try:
        query_params = {
            "Select": "COUNT",
            "KeyConditionExpression": key_condition,
        }

        if index_name:
            query_params["IndexName"] = index_name

        total_count = 0
        while True:
            response = table.query(**query_params)
            total_count += response.get("Count", 0)
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            else:
                break
                
        return total_count
    except ClientError as e:
        print(f"Error querying item count: {e}")
        return 0


def batch_write(items: List[Dict[str, Any]]) -> bool:
    """Batch write multiple items (up to 25 at a time)"""
    table = get_dynamodb_table()

    try:
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        return True
    except ClientError as e:
        print(f"Error batch writing items: {e}")
        return False


def batch_get(keys: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Batch get multiple items (up to 100 at a time)"""
    table = get_dynamodb_table()

    try:
        response = table.meta.client.batch_get_item(
            RequestItems={table.name: {"Keys": keys}}
        )
        return response.get("Responses", {}).get(table.name, [])
    except ClientError as e:
        print(f"Error batch getting items: {e}")
        return []


# Time-series helpers
def get_hour_bucket(timestamp: float) -> str:
    """Get hour bucket string from timestamp for time-series data"""
    from datetime import datetime

    dt = datetime.utcfromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%dT%H")


def get_time_buckets_range(start_timestamp: float, end_timestamp: float) -> List[str]:
    """Get list of hour buckets between two timestamps"""
    from datetime import datetime, timedelta

    start = datetime.utcfromtimestamp(start_timestamp)
    end = datetime.utcfromtimestamp(end_timestamp)

    buckets = []
    current = start.replace(minute=0, second=0, microsecond=0)

    while current <= end:
        buckets.append(current.strftime("%Y-%m-%dT%H"))
        current += timedelta(hours=1)

    return buckets
