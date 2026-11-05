import base64
from email.message import EmailMessage
from email.policy import SMTP

from googleapiclient.discovery import build
from gmail.models import GmailDraft, GmailMessage
from gmail.parser import parse_message


def _recipient_addresses(value: str) -> set[str]:
    """Parse mailbox addresses for comparison, ignoring display names."""
    message = EmailMessage(policy=SMTP)
    message["To"] = value
    header = message["To"]
    if header.defects or not header.addresses or any(
        not address.username or not address.domain for address in header.addresses
    ):
        raise ValueError("Cannot validate reply recipient: invalid or missing email address")
    return {address.addr_spec.casefold() for address in header.addresses}


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


    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
    ) -> GmailDraft:
        """Create an unsent plain-text draft.

        For a threaded draft, subject must match the latest message's subject.
        For incoming messages, recipients must match Reply-To, or From when
        Reply-To is absent. Display names and address case are ignored.
        SENT/DRAFT messages skip this check because their sender is the user.
        Missing reply metadata or mismatched subject/recipients raises ValueError.
        """
        message = EmailMessage(policy=SMTP)
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        if thread_id is not None:
            raw_thread = self.service.users().threads().get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["Message-ID", "References", "In-Reply-To", "Subject", "From", "Reply-To"],
            ).execute()
            messages = raw_thread.get("messages", [])
            if not messages:
                raise ValueError("Cannot reply to an empty thread")
            parent = max(messages, key=lambda item: int(item["internalDate"]))
            headers = {
                header["name"].lower(): header["value"]
                for header in parent.get("payload", {}).get("headers", [])
            }
            parent_id = headers.get("message-id")
            if not parent_id:
                raise ValueError("Thread's latest message has no Message-ID header")
            if subject != headers.get("subject", ""):
                raise ValueError("Draft subject must match the thread's latest subject")
            if not {"SENT", "DRAFT"}.intersection(parent.get("labelIds", [])):
                suggested = headers.get("reply-to", headers.get("from", ""))
                if _recipient_addresses(to) != _recipient_addresses(suggested):
                    raise ValueError(
                        f"Draft recipient does not match the latest message's reply recipient: {suggested}"
                    )
            message["In-Reply-To"] = parent_id
            references = headers.get("references") or headers.get("in-reply-to", "")
            message["References"] = f"{references} {parent_id}".strip()

        draft_message = {
            "raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii"),
        }
        if thread_id is not None:
            draft_message["threadId"] = thread_id
        draft = self.service.users().drafts().create(
            userId="me", body={"message": draft_message},
        ).execute()

        return GmailDraft(
            id=draft["id"],
            message_id=draft["message"]["id"],
            thread_id=draft["message"]["threadId"],
            to=to,
            subject=subject,
            body=body,
        )

    # send_draft()
    # archive_message()
    # add_label()
