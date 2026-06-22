"""Art Critic Lambda — scores artwork quality via Bedrock Haiku vision."""

import json
import os
import base64
import boto3
from decimal import Decimal

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

ARTIST_FIDELITY = {
    "sam_francis": "Sam Francis: bold saturated color fields, energetic splashes/splatters, luminous negative space, color pooling at edges. Palette: pure saturated cadmium yellow, ultramarine blue, cadmium red. White space is luminous.",
    "gerhard_richter": "Gerhard Richter: sweeping squeegee strokes layering and revealing color beneath. Horizontal/vertical paint drags. Rich complex color layering. No discrete shapes — continuous dragged paint.",
    "norman_lewis": "Norman Lewis: calligraphic abstraction — teeming small marks, glyphs, and tiny figures massing in horizontal processions across deep atmospheric grounds (blue-black, smoky umber). Restrained palette: dark ground with white/ochre/crimson/cerulean notations. Atmospheric veils, jazz rhythm, crowds dissolving into abstraction. No hard edges, no literal figures.",
    "hilma_af_klint": "Hilma af Klint: mystical geometric abstraction, biomorphic spirals, circles within circles, botanical symmetry. Pastels alongside jewel tones. Gold/ochre as sacred element.",
    "wassily_kandinsky": "Wassily Kandinsky: geometric shapes (circles, triangles, lines) in musical harmony. Bold primaries on muted backgrounds. Shapes suggest rhythm and movement.",
    "helen_frankenthaler": "Helen Frankenthaler: stain painting — color soaked/bled into canvas. Transparent washes pooling and overlapping. No visible brushstrokes — poured, absorbed color.",
    "piet_mondrian": "Piet Mondrian: ONLY horizontal/vertical black lines dividing primary color rectangles (red, blue, yellow) and white fields. Asymmetric balance. No curves, no diagonals, no gradients.",
    "yayoi_kusama": "Yayoi Kusama: obsessive dots and circles, infinity nets, polka dots in vivid colors on contrasting backgrounds. Everything circular — no straight lines or angular forms.",
    "mark_rothko": "Mark Rothko: 2-3 soft-edged horizontal color bands floating on canvas. Deeply saturated, glowing from within. No hard edges, no patterns, no detail. Reduction and contemplation.",
    "bridget_riley": "Bridget Riley: precise geometric op art patterns creating optical movement/vibration. Undulating lines, chevrons, systematic color progressions. Mathematically controlled.",
    "kazimir_malevich": "Kazimir Malevich: suprematist flat geometric forms (squares, circles, crosses) floating in dominant white space. Bold flat colors, no gradients, no texture. Dynamic diagonals.",
    "lesley_tannahill": "Lesley Tannahill: dense palimpsest layers — paint over paint, scraping back, fragments emerging from abstract fields. Muted California palette (warm greys, dusty pinks, ochre). Should feel WORKED, not clean.",
    "arshile_gorky": "Arshile Gorky: biomorphic forms hovering between figuration and abstraction — organic shapes dissolving at edges, neither fully figure nor fully abstract. Thin washes with dense tortured marks on top. Earth tones (ochre, sienna, umber) with surprising acid green or cerulean accents. Anguished and lyrical simultaneously.",
    "willem_de_kooning": "Willem de Kooning: aggressive slashing brushstrokes, visceral gestural energy, flesh tones (pink, yellow, orange) colliding with blacks and whites. Paint should feel FLUNG and DRAGGED. High-chroma clashes, no peaceful passages. Fragmentary figuration dissolving into pure paint marks.",
    "joan_mitchell": "Joan Mitchell: dense clusters of gestural brushwork in vibrant saturated color (viridian green, cobalt blue, cadmium yellow) against luminous open ground. Marks grouped and blooming like memory of landscape. Loaded paint, physical presence, exuberant color — never muddy. Emotional directness.",
    "mark_tobey": "Mark Tobey: dense networks of white/cream calligraphic marks on dark grounds (blue-black, umber-black). All-over composition — no focal point, every inch equally alive with teeming marks. Highly restrained palette (dark ground + white writing). Should feel like looking at city lights, cosmic networks, or spiritual illumination.",
    "peter_max": "Peter Max: bold heavy continuous black outlines on all forms, DayGlo full-spectrum neons (hot magenta, electric yellow, acid green, cobalt blue, vivid orange, UV violet) on black cosmic ground. Recognizable cosmic imagery — Earth as glowing orb, rainbow arcs, starfields, hearts. Maximum saturation, psychedelic 60s energy. Every line connects — flow-of-the-line draftsmanship. Zero subtlety, unapologetic neon joy.",
}

def build_critic_prompt(artist_key=None):
    fidelity_section = ""
    if artist_key and artist_key in ARTIST_FIDELITY:
        fidelity_section = f"""
- Artist fidelity (does this look like it could be by {artist_key.replace('_', ' ').title()}? Key markers: {ARTIST_FIDELITY[artist_key]})
"""
    return f"""You are an art critic evaluating a generative artwork created from atmospheric weather data.

Score this artwork on a scale of 1-10 across these criteria:
- Composition (balance, use of space, visual flow)
- Color harmony (palette cohesion, contrast, mood)
- Complexity (detail, layering, technique)
- Emotional impact (does it evoke a response?){fidelity_section}
Respond with ONLY valid JSON:
{{
  "composition": <1-10>,
  "color": <1-10>,
  "complexity": <1-10>,
  "impact": <1-10>,{'"artist_fidelity": <1-10>,' if artist_key else ''}
  "overall": <1-10>,
  "one_liner": "<one sentence critique>"
}}"""


# Legacy fallback
CRITIC_PROMPT = build_critic_prompt()


def handler(event, context):
    """Score a single artwork. Called after PNG render."""
    run_id = event.get("run_id", event.get("date", "unknown"))
    slug = event["slug"]
    artist_key = event.get("artist", "")

    # Build artist-aware prompt
    prompt = build_critic_prompt(artist_key) if artist_key else CRITIC_PROMPT

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
                    {"type": "text", "text": prompt},
                ],
            }],
        }),
    )

    result = json.loads(response["body"].read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]

    try:
        scores = json.loads(text.strip())
    except json.JSONDecodeError:
        import re
        cleaned = re.sub(r",\s*([}\]])", r"\1", text.strip())
        scores = json.loads(cleaned)

    overall = scores.get("overall", 5)
    fidelity = scores.get("artist_fidelity", None)

    # Store score in DynamoDB
    table = dynamodb.Table(TABLE_NAME)
    table.update_item(
        Key={"PK": f"WEATHER#{run_id}", "SK": slug},
        UpdateExpression="SET quality_score = :qs, quality_detail = :qd",
        ExpressionAttributeValues={
            ":qs": Decimal(str(overall)),
            ":qd": json.dumps(scores),
        },
    )

    fidelity_msg = f", fidelity={fidelity}/10" if fidelity else ""
    low_fidelity = fidelity is not None and int(fidelity) <= 3
    if low_fidelity:
        print(f"[CRITIC] LOW FIDELITY WARNING: {run_id}/{slug} scored {fidelity}/10 for artist fidelity — does not resemble {artist_key}")

    print(f"[CRITIC] {run_id}/{slug}: {overall}/10{fidelity_msg} — {scores.get('one_liner', '')}")
    return {"run_id": run_id, "slug": slug, "quality_score": overall, "artist_fidelity": fidelity, "low_fidelity": low_fidelity, "detail": scores}
