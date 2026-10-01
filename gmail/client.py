
from googleapiclient.discovery import build
from gmail.models import GmailMessage
from gmail.parser import parse_message


class GmailClient:
    def __init__(self, credentials):
        self.service = build(
            "gmail",
            "v1",
            credentials=credentials
        )

    def search_messages(self, query: str, max_results: int = 10):
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

    def get_message(self, message_id: str) -> GmailMessage:
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

        return parse_message(raw)

    # get_thread()
    # create_draft()
    # send_draft()
    # archive_message()
    # add_label()
