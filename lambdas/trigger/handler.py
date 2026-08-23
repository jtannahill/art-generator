"""Trigger Lambda — starts the art pipeline via HTTP."""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import boto3

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
COOLDOWN_HOURS = 2
ARTIST_RE = re.compile(r"^[a-z_]{3,40}$")


def _json(status_code, body):
    return {"statusCode": status_code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def verify_turnstile(token, remote_ip):
    """Server-side Cloudflare Turnstile check. A public trigger URL with no human check
    let bots start ~$3 pipeline runs at will (Aug 2026); every start now needs a token."""
    if not TURNSTILE_SECRET_KEY:
        return False
    if not token or len(token) > 2048:
        return False
    data = urllib.parse.urlencode({"secret": TURNSTILE_SECRET_KEY, "response": token, "remoteip": remote_ip or ""}).encode()
    req = urllib.request.Request("https://challenges.cloudflare.com/turnstile/v0/siteverify", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return bool(json.loads(resp.read()).get("success"))
    except Exception as e:
        print(f"Turnstile verify error: {e}")
        return False


def handler(event, context):
    """Lambda function URL handler — triggers the Step Function."""
    body = {}
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        pass
    query = event.get("queryStringParameters") or {}
    token = body.get("token") or query.get("token")
    remote_ip = (event.get("requestContext") or {}).get("http", {}).get("sourceIp")
    if not verify_turnstile(token, remote_ip):
        return _json(403, {"status": "forbidden", "message": "Human verification failed - reload and try again"})

    artist = body.get("artist") or query.get("artist") or "sam_francis"
    if not ARTIST_RE.match(artist):
        return _json(400, {"status": "bad_artist"})

    sfn = boto3.client("stepfunctions")

    # Check if already running
    running = sfn.list_executions(
        stateMachineArn=STATE_MACHINE_ARN,
        statusFilter="RUNNING",
        maxResults=1,
    )
    if running.get("executions"):
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "already_running", "message": "Pipeline is already generating art"}),
        }

    # Check cooldown — last successful execution must be >2 hours ago
    recent = sfn.list_executions(
        stateMachineArn=STATE_MACHINE_ARN,
        statusFilter="SUCCEEDED",
        maxResults=1,
    )
    if recent.get("executions"):
        last_start = recent["executions"][0]["startDate"]
        if last_start.tzinfo is None:
            last_start = last_start.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last_start
        if elapsed < timedelta(hours=COOLDOWN_HOURS):
            remaining = timedelta(hours=COOLDOWN_HOURS) - elapsed
            mins = int(remaining.total_seconds() / 60)
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "cooldown", "message": f"Next generation available in {mins} minutes"}),
            }

    sfn_input = json.dumps({"artist": artist})

    resp = sfn.start_execution(stateMachineArn=STATE_MACHINE_ARN, input=sfn_input)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "started", "executionArn": resp["executionArn"]}),
    }
