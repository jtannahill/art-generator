"""Art Critic Lambda — scores artwork quality via Bedrock Haiku vision."""

import json
import os
import base64
import boto3

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

CRITIC_PROMPT = """You are an art critic evaluating a generative artwork created from atmospheric weather data.

Score this artwork on a scale of 1-10 across these criteria:
- Composition (balance, use of space, visual flow)
- Color harmony (palette cohesion, contrast, mood)
- Complexity (detail, layering, technique)
- Emotional impact (does it evoke a response?)

Respond with ONLY valid JSON:
{
  "composition": <1-10>,
  "color": <1-10>,
  "complexity": <1-10>,
  "impact": <1-10>,
  "overall": <1-10>,
  "one_liner": "<one sentence critique>"
}"""


def handler(event, context):
    """Score a single artwork. Called after PNG render."""
    run_id = event["run_id"]
    slug = event["slug"]

    # Download the preview PNG
    s3_key = f"weather/{run_id}/{slug}/preview-2048.png"
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
        png_data = obj["Body"].read()
    except Exception:
        # Try site/ prefix
        try:
            obj = s3.get_object(Bucket=BUCKET_NAME, Key=f"site/{s3_key}")
            png_data = obj["Body"].read()
        except Exception as e:
            print(f"[SKIP] No PNG for {run_id}/{slug}: {e}")
            return {"run_id": run_id, "slug": slug, "quality_score": None}

    # Send to Haiku vision
    img_b64 = base64.b64encode(png_data).decode()
    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": CRITIC_PROMPT},
                ],
            }],
        }),
    )

    result = json.loads(response["body"].read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]

    scores = json.loads(text.strip())
    overall = scores.get("overall", 5)

    # Store score in DynamoDB
    table = dynamodb.Table(TABLE_NAME)
    table.update_item(
        Key={"PK": f"WEATHER#{run_id}", "SK": slug},
        UpdateExpression="SET quality_score = :qs, quality_detail = :qd",
        ExpressionAttributeValues={
            ":qs": overall,
            ":qd": json.dumps(scores),
        },
    )

    print(f"[CRITIC] {run_id}/{slug}: {overall}/10 — {scores.get('one_liner', '')}")
    return {"run_id": run_id, "slug": slug, "quality_score": overall, "detail": scores}
