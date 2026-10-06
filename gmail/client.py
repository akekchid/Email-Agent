
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
        """Search for Gmail messages matching the given query."""
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
        """Retrieve a Gmail message by its ID."""
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

    def get_thread(self, thread_id: str) -> list[GmailMessage]:
        """Retrieve all messages in a Gmail thread by its ID."""
        raw_thread = (
            self.service.users()
            .threads()
            .get(
                userId="me",
                id=thread_id,
                format="full",
            )
            .execute()
        )

        messages = [
            parse_message(raw)
            for raw in raw_thread.get("messages", [])
        ]

        return sorted(
            messages,
            key=lambda message: message.timestamp,
        )    


    # create_draft()
    # send_draft()
    # archive_message()
    # add_label()
