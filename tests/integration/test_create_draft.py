"""Live Gmail draft test; opt in with RUN_GMAIL_INTEGRATION=1.

Run from the repository root:
    RUN_GMAIL_INTEGRATION=1 .venv/bin/python -m unittest discover \
        -s tests/integration -p test_create_draft.py -v

GMAIL_TEST_TOKEN_FILE may point to an authorized-user token (default token.json).
It needs gmail.readonly + gmail.compose, or gmail.modify. This test never sends
mail. It selects the latest email from the school account to the Gmail account.
"""

import os
import unittest
from email.utils import getaddresses
from pathlib import Path
from uuid import uuid4

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

from gmail.client import GmailClient
from gmail.parser import extract_body


@unittest.skipUnless(os.environ.get("RUN_GMAIL_INTEGRATION") == "1", "Live Gmail test is opt-in")
class CreateDraftIntegrationTests(unittest.TestCase):
    def test_reply_draft_appears_in_original_conversation(self):
        token_path = Path(os.environ.get("GMAIL_TEST_TOKEN_FILE", "token.json"))
        self.assertTrue(token_path.is_file(), "Authorize Gmail first; no token file found")
        credentials = Credentials.from_authorized_user_file(str(token_path))
        scopes = set(credentials.scopes or [])
        broad_access = bool(scopes & {
            "https://mail.google.com/", "https://www.googleapis.com/auth/gmail.modify",
        })
        self.assertTrue(broad_access or {
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        }.issubset(scopes), "Reauthorize with gmail.readonly + gmail.compose (or gmail.modify); the saved token lacks draft permissions")
        if not credentials.valid:
            credentials.refresh(Request())

        gmail = GmailClient(credentials)
        profile = gmail.service.users().getProfile(userId="me").execute()
        self.assertEqual(profile["emailAddress"].casefold(), "alekokek@gmail.com")
        refs = gmail.search_messages(
            query="from:akekchid@ucsc.edu to:alekokek@gmail.com -in:trash -in:spam",
            max_results=1,
        )
        self.assertTrue(refs, "No email from akekchid@ucsc.edu to alekokek@gmail.com found")
        original = gmail.get_message(refs[0]["id"])
        self.assertEqual(original.sender_email.casefold(), "akekchid@ucsc.edu")
        self.assertIn("alekokek@gmail.com", {
            address.casefold() for _, address in getaddresses([original.recipient])
        })
        thread_id = original.thread_id
        thread = gmail.service.users().threads().get(
            userId="me", id=thread_id, format="metadata",
            metadataHeaders=["Message-ID", "Subject", "From", "Reply-To"],
        ).execute()
        latest = max(thread["messages"], key=lambda message: int(message["internalDate"]))
        self.assertEqual(latest["id"], original.id,
                         "Use a test conversation whose latest message is the school email")
        headers = {header["name"].lower(): header["value"]
                   for header in latest["payload"].get("headers", [])}
        parent_id = headers["message-id"]
        recipient = headers.get("reply-to", headers["from"])
        body = f"Automated draft integration test {uuid4()}. This draft will be deleted."

        draft = gmail.create_draft(
            to=recipient, subject=original.subject, body=body, thread_id=thread_id,
        )
        # Register cleanup before any assertions so a failed verification still
        # deletes precisely the draft created by this test, never existing drafts.
        self.addCleanup(self.delete_and_verify, gmail, draft.id)
        self.assertEqual(draft.thread_id, thread_id)

        saved = gmail.service.users().drafts().get(
            userId="me", id=draft.id, format="full",
        ).execute()
        message = saved["message"]
        self.assertEqual(message["id"], draft.message_id)
        self.assertEqual(message["threadId"], thread_id)
        self.assertIn("DRAFT", message["labelIds"])
        self.assertEqual(extract_body(message["payload"]).strip(), body)
        saved_headers = {header["name"].lower(): header["value"]
                         for header in message["payload"]["headers"]}
        self.assertEqual(saved_headers["in-reply-to"], parent_id)

        conversation = gmail.service.users().threads().get(
            userId="me", id=thread_id, format="minimal",
        ).execute()
        ids = {item["id"] for item in conversation["messages"]}
        self.assertIn(original.id, ids)
        self.assertIn(draft.message_id, ids, "Draft is not in the original Gmail conversation")

    def delete_and_verify(self, gmail, draft_id):
        gmail.service.users().drafts().delete(userId="me", id=draft_id).execute()
        with self.assertRaises(HttpError) as error:
            gmail.service.users().drafts().get(userId="me", id=draft_id).execute()
        self.assertEqual(error.exception.resp.status, 404, "Draft deletion was not confirmed")


if __name__ == "__main__":
    unittest.main()
