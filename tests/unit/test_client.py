"""Offline Gmail client tests using mocked API resources."""

import base64
import unittest
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock, call, patch, sentinel

from googleapiclient.errors import HttpError
from httplib2 import Response

from gmail.client import GmailClient
from gmail.models import GmailDraft, GmailMessage


class GmailClientTests(unittest.TestCase):
    def setUp(self):
        # Patch where build is imported so construction never contacts Gmail.
        self.build = self.enterContext(patch("gmail.client.build"))
        self.messages = Mock(spec=["list", "get"])
        self.build.return_value.users.return_value.messages.return_value = self.messages
        self.threads = Mock(spec=["get"])
        self.build.return_value.users.return_value.threads.return_value = self.threads
        self.drafts = Mock(spec=["create"])
        self.build.return_value.users.return_value.drafts.return_value = self.drafts
        self.drafts.create.return_value.execute.return_value = {
            "id": "draft-1", "message": {"id": "draft-message-1", "threadId": "thread-1"},
        }
        self.client = GmailClient(sentinel.credentials)

    def decoded_draft(self):
        self.drafts.create.assert_called_once()
        kwargs = self.drafts.create.call_args.kwargs
        self.assertEqual(kwargs["userId"], "me")
        encoded = kwargs["body"]["message"]
        message = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(encoded["raw"]),
        )
        return encoded, message

    def reply_metadata(self):
        return {"messages": [
            {
                "internalDate": "1000",
                "payload": {"headers": [
                    {"name": "Message-ID", "value": "<latest@example.com>"},
                    {"name": "Subject", "value": "Coffee"},
                    {"name": "From", "value": "Jane <jane@example.com>"},
                    {"name": "References", "value": "<original@example.com>"},
                ]},
            },
            {
                "internalDate": "900",
                "payload": {"headers": [
                    {"name": "Message-ID", "value": "<original@example.com>"},
                    {"name": "Subject", "value": "Coffee"},
                ]},
            },
        ]}

    def test_create_draft_encodes_unicode_content_and_returns_draft(self):
        result = self.client.create_draft("Jane <jane@example.com>", "Café ☕", "Hi Jane,\nCoffee tomorrow? ☕")

        encoded, message = self.decoded_draft()
        self.assertEqual(set(encoded), {"raw"})
        self.assertEqual(str(message["To"]), "Jane <jane@example.com>")
        self.assertEqual(str(message["Subject"]), "Café ☕")
        self.assertEqual(message.get_content_type(), "text/plain")
        self.assertEqual(message.get_content(), "Hi Jane,\r\nCoffee tomorrow? ☕\r\n")
        self.assertIsNone(message["In-Reply-To"])
        self.threads.get.assert_not_called()
        self.drafts.create.return_value.execute.assert_called_once_with()
        self.assertEqual(result, GmailDraft(
            id="draft-1", message_id="draft-message-1", thread_id="thread-1",
            to="Jane <jane@example.com>", subject="Café ☕", body="Hi Jane,\nCoffee tomorrow? ☕",
        ))

    def test_create_draft_sets_reply_headers_from_latest_message(self):
        self.threads.get.return_value.execute.return_value = self.reply_metadata()

        self.client.create_draft("jane@example.com", "Coffee", "Sounds good!", thread_id="thread-1")

        encoded, message = self.decoded_draft()
        self.assertEqual(encoded["threadId"], "thread-1")
        self.assertEqual(message["In-Reply-To"], "<latest@example.com>")
        self.assertEqual(message["References"], "<original@example.com> <latest@example.com>")
        self.threads.get.assert_called_once_with(
            userId="me", id="thread-1", format="metadata",
            metadataHeaders=["Message-ID", "References", "In-Reply-To", "Subject", "From", "Reply-To"],
        )

    def test_create_draft_prefers_reply_to_and_preserves_supplied_recipient(self):
        metadata = self.reply_metadata()
        metadata["messages"][0]["payload"]["headers"].append(
            {"name": "rEpLy-To", "value": "Coffee Team <coffee@example.com>"},
        )
        self.threads.get.return_value.execute.return_value = metadata

        result = self.client.create_draft("Team <COFFEE@example.com>", "Coffee", "Yes", "thread-1")

        _, message = self.decoded_draft()
        self.assertEqual(str(message["To"]), "Team <COFFEE@example.com>")
        self.assertEqual(result.to, "Team <COFFEE@example.com>")

    def test_create_draft_rejects_from_when_reply_to_is_present(self):
        metadata = self.reply_metadata()
        metadata["messages"][0]["payload"]["headers"].append(
            {"name": "Reply-To", "value": "coffee@example.com"},
        )
        self.threads.get.return_value.execute.return_value = metadata

        with self.assertRaisesRegex(ValueError, "coffee@example.com"):
            self.client.create_draft("jane@example.com", "Coffee", "Yes", "thread-1")
        self.drafts.create.assert_not_called()

    def test_create_draft_rejects_wrong_or_additional_reply_recipients(self):
        self.threads.get.return_value.execute.return_value = self.reply_metadata()
        for to in ("other@example.com", "jane@example.com, other@example.com"):
            with self.subTest(to=to), self.assertRaisesRegex(ValueError, "jane@example.com"):
                self.client.create_draft(to, "Coffee", "Yes", "thread-1")
        self.drafts.create.assert_not_called()

    def test_create_draft_accepts_multiple_reply_to_addresses_in_any_order(self):
        metadata = self.reply_metadata()
        metadata["messages"][0]["payload"]["headers"].append({
            "name": "Reply-To", "value": "Jane <jane@example.com>, Team <coffee@example.com>",
        })
        self.threads.get.return_value.execute.return_value = metadata

        self.client.create_draft("coffee@example.com, jane@example.com", "Coffee", "Yes", "thread-1")

        self.drafts.create.assert_called_once()

    def test_create_draft_rejects_missing_or_invalid_reply_metadata(self):
        for reply_to in (None, "", "not-an-address", "Jane <broken"):
            with self.subTest(reply_to=reply_to):
                metadata = self.reply_metadata()
                headers = metadata["messages"][0]["payload"]["headers"]
                if reply_to is None:
                    headers[:] = [header for header in headers if header["name"] != "From"]
                else:
                    headers.append({"name": "Reply-To", "value": reply_to})
                self.threads.get.return_value.execute.return_value = metadata
                with self.assertRaises(ValueError):
                    self.client.create_draft("jane@example.com", "Coffee", "Yes", "thread-1")
        self.drafts.create.assert_not_called()

    def test_create_draft_rejects_malformed_intended_recipient(self):
        self.threads.get.return_value.execute.return_value = self.reply_metadata()
        with self.assertRaises(ValueError):
            self.client.create_draft("Jane <jane@example.com", "Coffee", "Yes", "thread-1")
        self.drafts.create.assert_not_called()

    def test_create_draft_does_not_treat_outgoing_sender_as_reply_recipient(self):
        for label in ("SENT", "DRAFT"):
            with self.subTest(label=label):
                metadata = self.reply_metadata()
                parent = metadata["messages"][0]
                parent["labelIds"] = [label]
                for header in parent["payload"]["headers"]:
                    if header["name"] == "From":
                        header["value"] = "me@example.com"
                self.threads.get.return_value.execute.return_value = metadata

                result = self.client.create_draft("jane@example.com", "Coffee", "Yes", "thread-1")

                self.assertEqual(result.to, "jane@example.com")
        self.assertEqual(self.drafts.create.call_count, 2)

    def test_create_draft_supports_parent_without_references(self):
        metadata = self.reply_metadata()
        metadata["messages"][0]["payload"]["headers"].pop()
        self.threads.get.return_value.execute.return_value = metadata

        self.client.create_draft("jane@example.com", "Coffee", "Yes", thread_id="thread-1")

        _, message = self.decoded_draft()
        self.assertEqual(message["References"], "<latest@example.com>")

    def test_create_draft_rejects_unusable_thread_before_creating(self):
        missing_id = self.reply_metadata()
        missing_id["messages"][0]["payload"]["headers"].pop(0)
        for metadata, subject in (({}, "Coffee"), (missing_id, "Coffee"), (self.reply_metadata(), "Other topic")):
            with self.subTest(metadata=metadata, subject=subject):
                self.threads.get.return_value.execute.return_value = metadata
                with self.assertRaises(ValueError):
                    self.client.create_draft("jane@example.com", subject, "Hi", thread_id="thread-1")
                self.drafts.create.assert_not_called()

    def test_create_draft_allows_empty_subject_and_body(self):
        result = self.client.create_draft("jane@example.com", "", "")

        _, message = self.decoded_draft()
        self.assertEqual(str(message["Subject"]), "")
        self.assertEqual(message.get_content(), "\r\n")
        self.assertEqual(result.body, "")

    def test_create_draft_rejects_header_injection(self):
        for to, subject in (("jane@example.com\r\nBcc: other@example.com", "Hi"),
                            ("jane@example.com", "Hi\nBcc: other@example.com")):
            with self.subTest(to=to, subject=subject), self.assertRaises(ValueError):
                self.client.create_draft(to, subject, "Hello")
        self.drafts.create.assert_not_called()

    def test_create_draft_propagates_api_errors(self):
        error = HttpError(Response({"status": "403"}), b'{"error": {"message": "Forbidden"}}')
        self.drafts.create.return_value.execute.side_effect = error

        with self.assertRaises(HttpError) as raised:
            self.client.create_draft("jane@example.com", "Coffee", "Hello")

        self.assertIs(raised.exception, error)

    def test_create_draft_does_not_create_when_thread_lookup_fails(self):
        error = HttpError(Response({"status": "404"}), b'{"error": {"message": "Not found"}}')
        self.threads.get.return_value.execute.side_effect = error

        with self.assertRaises(HttpError) as raised:
            self.client.create_draft("jane@example.com", "Coffee", "Hello", thread_id="missing")

        self.assertIs(raised.exception, error)
        self.drafts.create.assert_not_called()

    def test_builds_gmail_service_with_supplied_credentials(self):
        self.build.assert_called_once_with("gmail", "v1", credentials=sentinel.credentials)

    def test_search_messages_returns_matches_with_default_limit(self):
        matches = [
            {"id": "message-1", "threadId": "thread-1"},
            {"id": "message-2", "threadId": "thread-2"},
        ]
        request = self.messages.list.return_value
        request.execute.return_value = {"messages": matches, "resultSizeEstimate": 2}

        result = self.client.search_messages("is:unread")

        self.assertEqual(result, matches)
        self.messages.list.assert_called_once_with(userId="me", q="is:unread", maxResults=10)
        request.execute.assert_called_once_with()

    def test_search_messages_passes_custom_limit_and_query_unchanged(self):
        query = 'from:ada@example.com subject:"Project update" after:2024/01/01'
        self.messages.list.return_value.execute.return_value = {"messages": []}

        self.client.search_messages(query, max_results=25)

        self.messages.list.assert_called_once_with(userId="me", q=query, maxResults=25)

    def test_search_messages_returns_empty_list_when_no_matches(self):
        for response in ({}, {"resultSizeEstimate": 0}, {"messages": []}):
            with self.subTest(response=response):
                self.messages.list.return_value.execute.return_value = response
                self.assertEqual(self.client.search_messages("is:unread"), [])

    def test_search_messages_accepts_empty_query(self):
        self.messages.list.return_value.execute.return_value = {"messages": []}

        self.assertEqual(self.client.search_messages(""), [])

        self.messages.list.assert_called_once_with(userId="me", q="", maxResults=10)

    def test_search_messages_propagates_api_errors(self):
        error = HttpError(Response({"status": "403"}), b'{"error": {"message": "Forbidden"}}')
        self.messages.list.return_value.execute.side_effect = error

        with self.assertRaises(HttpError) as raised:
            self.client.search_messages("is:unread")

        self.assertIs(raised.exception, error)

    def test_get_message_requests_full_message_and_returns_parser_result(self):
        raw = {"id": "message-1", "payload": {}}
        request = self.messages.get.return_value
        request.execute.return_value = raw
        with patch("gmail.client.parse_message", return_value=sentinel.parsed_message) as parse:
            result = self.client.get_message("message-1")

        self.messages.get.assert_called_once_with(userId="me", id="message-1", format="full")
        request.execute.assert_called_once_with()
        parse.assert_called_once_with(raw)
        self.assertIs(result, sentinel.parsed_message)

    def test_get_message_parses_realistic_api_response(self):
        # Exercise the real parser once to verify the client/parser boundary.
        self.messages.get.return_value.execute.return_value = {
            "id": "message-1",
            "threadId": "thread-1",
            "internalDate": "1704067200123",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Ada <ada@example.com>"},
                    {"name": "To", "value": "grace@example.com"},
                    {"name": "Subject", "value": "Project update"},
                ],
                "body": {"data": base64.urlsafe_b64encode(b"  Hello &amp; welcome!  ").decode("ascii")},
            },
        }

        result = self.client.get_message("message-1")

        self.assertEqual(result, GmailMessage(
            id="message-1",
            thread_id="thread-1",
            sender_name="Ada",
            sender_email="ada@example.com",
            recipient="grace@example.com",
            subject="Project update",
            body="Hello & welcome!",
            timestamp=datetime(2024, 1, 1, 0, 0, 0, 123000, tzinfo=timezone.utc),
        ))

    def test_get_message_propagates_api_errors_without_parsing(self):
        error = HttpError(Response({"status": "404"}), b'{"error": {"message": "Not found"}}')
        self.messages.get.return_value.execute.side_effect = error

        with patch("gmail.client.parse_message") as parse:
            with self.assertRaises(HttpError) as raised:
                self.client.get_message("missing-message")

        self.assertIs(raised.exception, error)
        parse.assert_not_called()

    def test_get_message_propagates_parser_errors(self):
        raw = {"id": "message-1"}
        self.messages.get.return_value.execute.return_value = raw
        error = ValueError("Invalid message")

        with patch("gmail.client.parse_message", side_effect=error) as parse:
            with self.assertRaises(ValueError) as raised:
                self.client.get_message("message-1")

        self.assertIs(raised.exception, error)
        parse.assert_called_once_with(raw)

    def test_get_thread_requests_full_thread_and_sorts_parsed_messages(self):
        raw_messages = [{"id": "latest"}, {"id": "earliest"}, {"id": "middle"}]
        parsed = [
            Mock(spec=GmailMessage, timestamp=datetime(2024, 1, day, tzinfo=timezone.utc))
            for day in (3, 1, 2)
        ]
        request = self.threads.get.return_value
        request.execute.return_value = {"id": "thread-1", "messages": raw_messages}

        with patch("gmail.client.parse_message", side_effect=parsed) as parse:
            result = self.client.get_thread("thread-1")

        self.threads.get.assert_called_once_with(userId="me", id="thread-1", format="full")
        request.execute.assert_called_once_with()
        self.assertEqual(parse.call_args_list, [call(raw) for raw in raw_messages])
        self.assertEqual(result, [parsed[1], parsed[2], parsed[0]])

    def test_get_thread_returns_empty_list_when_messages_are_missing_or_empty(self):
        for response in ({}, {"id": "thread-1"}, {"id": "thread-1", "messages": []}):
            with self.subTest(response=response):
                self.threads.get.return_value.execute.return_value = response
                with patch("gmail.client.parse_message") as parse:
                    self.assertEqual(self.client.get_thread("thread-1"), [])
                parse.assert_not_called()

    def test_get_thread_returns_single_parsed_message(self):
        raw = {"id": "message-1"}
        parsed = Mock(spec=GmailMessage, timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.threads.get.return_value.execute.return_value = {"messages": [raw]}

        with patch("gmail.client.parse_message", return_value=parsed) as parse:
            result = self.client.get_thread("thread-1")

        self.assertEqual(result, [parsed])
        parse.assert_called_once_with(raw)

    def test_get_thread_preserves_order_for_equal_timestamps(self):
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
        parsed = [Mock(spec=GmailMessage, timestamp=timestamp) for _ in range(3)]
        self.threads.get.return_value.execute.return_value = {
            "messages": [{"id": "b"}, {"id": "a"}, {"id": "c"}],
        }

        with patch("gmail.client.parse_message", side_effect=parsed):
            result = self.client.get_thread("thread-1")

        self.assertEqual(result, parsed)

    def test_get_thread_parses_real_messages_and_sorts_by_converted_timestamp(self):
        self.threads.get.return_value.execute.return_value = {
            "id": "thread-1",
            "messages": [
                {
                    "id": message_id,
                    "threadId": "thread-1",
                    "internalDate": timestamp,
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode("ascii")},
                    },
                }
                for message_id, timestamp, body in (
                    ("later", "1000", "Second &amp; last"),
                    ("earlier", "900", "First"),
                )
            ],
        }

        result = self.client.get_thread("thread-1")

        self.assertEqual(result, [
            GmailMessage(
                id="earlier", thread_id="thread-1", sender_name="", sender_email="",
                recipient="", subject="", body="First",
                timestamp=datetime(1970, 1, 1, 0, 0, 0, 900000, tzinfo=timezone.utc),
            ),
            GmailMessage(
                id="later", thread_id="thread-1", sender_name="", sender_email="",
                recipient="", subject="", body="Second & last",
                timestamp=datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
            ),
        ])

    def test_get_thread_propagates_api_errors_without_parsing(self):
        error = HttpError(Response({"status": "404"}), b'{"error": {"message": "Not found"}}')
        self.threads.get.return_value.execute.side_effect = error

        with patch("gmail.client.parse_message") as parse:
            with self.assertRaises(HttpError) as raised:
                self.client.get_thread("missing-thread")

        self.assertIs(raised.exception, error)
        parse.assert_not_called()

    def test_get_thread_propagates_parser_errors(self):
        raw = {"id": "invalid-message"}
        self.threads.get.return_value.execute.return_value = {"messages": [raw]}
        error = ValueError("Invalid message")

        with patch("gmail.client.parse_message", side_effect=error) as parse:
            with self.assertRaises(ValueError) as raised:
                self.client.get_thread("thread-1")

        self.assertIs(raised.exception, error)
        parse.assert_called_once_with(raw)


if __name__ == "__main__":
    unittest.main()
