"""Offline Gmail client tests using mocked API resources."""

import base64
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, call, patch, sentinel

from googleapiclient.errors import HttpError
from httplib2 import Response

from gmail.client import GmailClient
from gmail.models import GmailMessage


class GmailClientTests(unittest.TestCase):
    def setUp(self):
        # Patch where build is imported so construction never contacts Gmail.
        self.build = self.enterContext(patch("gmail.client.build"))
        self.messages = Mock(spec=["list", "get"])
        self.build.return_value.users.return_value.messages.return_value = self.messages
        self.threads = Mock(spec=["get"])
        self.build.return_value.users.return_value.threads.return_value = self.threads
        self.client = GmailClient(sentinel.credentials)

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
