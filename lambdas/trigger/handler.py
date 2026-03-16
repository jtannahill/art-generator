"""Trigger Lambda — starts the art pipeline via HTTP."""
import json
import os
from datetime import datetime, timezone, timedelta

import boto3

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
COOLDOWN_HOURS = 2


def handler(event, context):
    """Lambda function URL handler — triggers the Step Function."""
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

    # Extract artist from query string
    query = event.get("queryStringParameters") or {}
    artist = query.get("artist", "sam_francis")
    sfn_input = json.dumps({"artist": artist})

    resp = sfn.start_execution(stateMachineArn=STATE_MACHINE_ARN, input=sfn_input)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "started", "executionArn": resp["executionArn"]}),
    }
