import base64
import html
import re


from datetime import datetime, timezone
from email.utils import parseaddr

from email.message import Message
from gmail.models import GmailMessage

def parse_timestamp(timestamp: str) -> datetime:
    return datetime.fromtimestamp(
        int(timestamp) / 1000,
        tz=timezone.utc,
    )

def clean_email_body(body: str) -> str:
    body = html.unescape(body)

    body = body.replace("\xa0", " ")
    body = body.replace("\r\n", "\n")

    # Collapse excesive spaces/tabs
    body = re.sub(r"[\t]+", " ", body)

    # Collapse excessive blank lines
    body = re.sub(r"\n{3,}", "\n\n", body)

    return body.strip()


def extract_body(payload: dict) -> str:
    """Extract inline text from a Gmail payload, preferring plain text to HTML."""
    bodies = {"text/plain": [], "text/html": []}

    def collect(part: dict) -> None:
        headers = Message()
        for header in part.get("headers", []):
            headers[header["name"]] = header["value"]

        if part.get("filename") or headers.get_content_disposition() == "attachment":
            return

        mime_type = part.get("mimeType", headers.get_content_type()).lower()
        data = part.get("body", {}).get("data")
        if mime_type in bodies and data:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            charset = headers.get_content_charset() or "utf-8"
            try:
                text = decoded.decode(charset, errors="replace")
            except LookupError:
                text = decoded.decode("utf-8", errors="replace")
            if text:
                bodies[mime_type].append(text)

        for child in part.get("parts", []):
            collect(child)

    collect(payload)
    return "\n".join(bodies["text/plain"] or bodies["text/html"])


def parse_message(raw_message: dict) -> GmailMessage:
    """ Parse a raw Gmail message dictionary into a GmailMessage object. """
    headers = raw_message["payload"].get("headers", [])

    header_map = {
        header["name"].lower(): header["value"]
        for header in headers    
    }

    body = extract_body(raw_message["payload"])
    body = clean_email_body(body)

    return GmailMessage(
        id=raw_message["id"],
        thread_id=raw_message["threadId"],
        sender_name=parseaddr(header_map.get("from", ""))[0],
        sender_email=parseaddr(header_map.get("from", ""))[1],
        recipient=header_map.get("to", ""),
        subject=header_map.get("subject", ""),
        body=body,
        timestamp=parse_timestamp(raw_message["internalDate"]),
    
    )
