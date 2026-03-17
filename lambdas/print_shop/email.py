"""SES email helpers for order confirmation and failure alerts."""

import boto3

SENDER = "art@jamestannahill.com"


def send_confirmation(to_email: str, artwork_title: str, edition_number: int, edition_limit: int, size_dims: str, artwork_url: str):
    """Send branded order confirmation to buyer."""
    subject = f"Your Limited Edition Print — {artwork_title}"
    body = f"""Thank you for your purchase.

Your print details:
- {artwork_title}
- Edition {edition_number} of {edition_limit}
- {size_dims}" on Hahnemühle German Etching 310gsm
- Certificate of Authenticity included

Your print is being prepared by our fine art print studio. Production typically takes 5-7 business days, after which it will be shipped to you with tracking.

View the original artwork: {artwork_url}

If you have any questions, reply to this email.

James Tannahill
art.jamestannahill.com
"""
    _send(to_email, subject, body)


def send_fulfillment_alert(order_id: str, error: str, customer_email: str, artwork_title: str):
    """Send alert to admin when fulfillment fails."""
    subject = f"ALERT: Print fulfillment failed — {order_id}"
    body = f"""Fulfillment failed for order {order_id}.

Customer: {customer_email}
Artwork: {artwork_title}
Error: {error}

The customer has been charged but the order was not submitted to theprintspace.
Manual intervention required — either retry the order or issue a refund.
"""
    _send(SENDER, subject, body)


def _send(to: str, subject: str, body: str):
    ses = boto3.client("ses", region_name="us-east-1")
    ses.send_email(
        Source=SENDER,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}},
        },
    )
