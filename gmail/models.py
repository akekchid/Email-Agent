from dataclasses import dataclass
from datetime import datetime

@dataclass
class GmailMessage:
    id: str
    thread_id: str

    sender_name: str
    sender_email: str

    recipient: str
    subject: str
    body: str
    timestamp: datetime