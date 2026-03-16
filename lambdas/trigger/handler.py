"""Trigger Lambda — starts the art pipeline via HTTP."""
import json
import os

import boto3

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")


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

    resp = sfn.start_execution(stateMachineArn=STATE_MACHINE_ARN, input="{}")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "started", "executionArn": resp["executionArn"]}),
    }
