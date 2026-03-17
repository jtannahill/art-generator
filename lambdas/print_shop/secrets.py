"""Load and cache secrets from AWS Secrets Manager on cold start."""

import json
import os

import boto3

_cache = {}
SECRET_ID = os.environ.get("SECRET_ID", "art-generator/print-shop")


def get_secrets() -> dict:
    """Return cached secrets dict with keys: stripe_secret_key, stripe_webhook_secret, tps_api_key."""
    if not _cache:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=SECRET_ID)
        _cache.update(json.loads(resp["SecretString"]))
    return _cache
