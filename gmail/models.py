from dataclasses import dataclass

@dataclass
class GmailMessage:
    id: str
    thread_id: str
    sender: str
    recipient: str
    subject: str
    body: str
    timestamp: str