"""Art Critic API — score any image via Bedrock Haiku vision."""
import json
import base64
import uuid
import urllib.request
import boto3

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

CRITIC_PROMPT = """Score this artwork on a scale of 1-10 across these criteria:
- Composition (balance, use of space, visual flow)
- Color harmony (palette cohesion, contrast, mood)
- Complexity (detail, layering, technique)
- Emotional impact (does it evoke a response?)

Respond with ONLY valid JSON:
{"composition":<1-10>,"color":<1-10>,"complexity":<1-10>,"impact":<1-10>,"overall":<1-10>,"critique":"<one sentence>"}"""


def handle_critique(event):
    body = json.loads(event.get("body", "{}"))
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    if body.get("image"):
        img_b64 = body["image"]
    elif body.get("image_url"):
        try:
            req = urllib.request.Request(body["image_url"], headers={"User-Agent": "art-api/1.0"})
            img_data = urllib.request.urlopen(req, timeout=10).read()
            if len(img_data) > 5 * 1024 * 1024:
                return _error(400, "image_too_large", "Image exceeds 5MB limit", request_id)
            img_b64 = base64.b64encode(img_data).decode()
        except Exception as e:
            return _error(400, "image_fetch_failed", f"Could not fetch image: {e}", request_id)
    else:
        return _error(400, "missing_image", "Provide 'image' (base64) or 'image_url'", request_id)

    # Detect media type from base64 header
    media_type = "image/png"
    if img_b64[:4] == "/9j/":
        media_type = "image/jpeg"
    elif img_b64[:4] == "UklG":
        media_type = "image/webp"

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": CRITIC_PROMPT},
            ]}],
        }),
    )

    result = json.loads(response["body"].read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]

    scores = json.loads(text.strip())
    scores["request_id"] = request_id
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(scores),
    }


def _error(status, code, message, request_id):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"error": {"code": code, "message": message, "request_id": request_id}}),
    }
