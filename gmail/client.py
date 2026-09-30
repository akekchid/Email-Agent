
from googleapiclient.discovery import build


class GmailClient:
    def __init__(self, credentials):
        self.service = build(
            "gmail",
            "v1",
            credentials=credentials
        )

    def list_messages(self, query: str | None = None, max_results: int = 10):
        result = (
            self.service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=max_results
            )
            .execute()
        )

        return result.get("messages", [])

    def parse_gmail_message(raw) -> EmailMessage:
        pass

    def get_message(self, message_id: str) -> EmailMessage:
        raw = (
            self.service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            )
            .execute()
        )

        return self.parse_gmail_message(raw)

    def get_thread(self, thread_id):
        pass

    def send_message(self, to, subject, body):
        pass

