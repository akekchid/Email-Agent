"""Offline orchestration tests; scripted model decisions are not live-model evals."""

import json
import runpy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import call, create_autospec, patch

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from gmail.client import GmailClient
from gmail.models import GmailMessage


class ScriptedToolModel(BaseChatModel):
    """Run assertions against each model input before returning its next decision."""

    steps: list[Any]
    seen: list[Any] = Field(default_factory=list)
    bound_tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted-email-orchestration-test"

    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        index = len(self.seen)
        self.seen.append(list(messages))
        if index >= len(self.steps):
            raise AssertionError("Agent made an unexpected extra model call")
        response = self.steps[index](messages)
        return ChatResult(generations=[ChatGeneration(message=response)])


def tool_call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": args, "id": call_id, "type": "tool_call",
    }])


def email(message_id, body, day=1):
    return GmailMessage(
        id=message_id, thread_id="jane-coffee-thread",
        sender_name="Jane", sender_email="jane@example.com",
        recipient="me@example.com", subject="Coffee",
        body=body, timestamp=datetime(2026, 10, day, tzinfo=timezone.utc),
    )


class EmailAgentOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.gmail = create_autospec(GmailClient, instance=True)
        # Keep environment loading and tracing from accessing local credentials
        # or exporting test runs, even when the developer has tracing enabled.
        self.enterContext(patch("dotenv.load_dotenv"))
        self.enterContext(patch.dict("os.environ", {
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
            "LANGCHAIN_TRACING": "false",
        }))

    def run_agent(self, prompt, steps):
        model = ScriptedToolModel(steps=steps)

        def build_agent(_model, **kwargs):
            return create_agent(model, **kwargs)

        # Execute the actual module and its tool registration, replacing only
        # the model at construction. No OpenAI model/client is constructed.
        path = Path(__file__).resolve().parents[2] / "email_agent.py"
        with patch("langchain.agents.create_agent", side_effect=build_agent):
            module = runpy.run_path(str(path))

        result = module["email_agent"].invoke(
            {"messages": [HumanMessage(content=prompt)]},
            context=module["EmailContext"](gmail_client=self.gmail),
            config={"recursion_limit": 10},
        )
        self.assertEqual(len(model.seen), len(steps))
        self.assertCountEqual(model.bound_tool_names, [
            "search_emails", "get_email", "get_email_thread",
        ])
        self.assertIsInstance(result["messages"][-1], AIMessage)
        self.assertEqual(result["messages"][-1].tool_calls, [])
        return result["messages"]

    def read_tool_result(self, messages, name, call_id):
        message = messages[-1]
        self.assertIsInstance(message, ToolMessage)
        self.assertEqual(message.name, name)
        self.assertEqual(message.tool_call_id, call_id)
        self.assertEqual(message.status, "success")
        # LangChain can retain an empty list directly as message content.
        return json.loads(message.content) if isinstance(message.content, str) else message.content

    def latest_email_scenario(self, retrieval_tool):
        prompt = "What is my latest email from Jane about?"
        latest = email("jane-latest", "Let's meet for coffee at 10 tomorrow.", day=2)
        refs = [{"id": latest.id, "threadId": latest.thread_id}]
        self.gmail.search_messages.return_value = refs
        self.gmail.get_message.return_value = latest
        self.gmail.get_thread.return_value = [latest]

        def search(messages):
            self.assertEqual(messages[-1].content, prompt)
            self.assertEqual(self.gmail.mock_calls, [])
            return tool_call("search_emails", {"query": "from:jane@example.com", "limit": 1}, "search")

        def retrieve(messages):
            found = self.read_tool_result(messages, "search_emails", "search")
            self.assertEqual(found, refs)
            self.assertEqual(self.gmail.mock_calls, [
                call.search_messages(query="from:jane@example.com", max_results=1),
            ])
            args = ({"message_id": found[0]["id"]} if retrieval_tool == "get_email"
                    else {"thread_id": found[0]["threadId"]})
            return tool_call(retrieval_tool, args, "retrieve")

        def answer(messages):
            fetched = self.read_tool_result(messages, retrieval_tool, "retrieve")
            message = fetched if retrieval_tool == "get_email" else fetched[-1]
            self.assertEqual(message["id"], latest.id)
            self.assertEqual(message["body"], latest.body)
            self.assertEqual(message["date"], latest.timestamp.isoformat())
            return AIMessage(content=f"Jane's latest email says: {message['body']}")

        messages = self.run_agent(prompt, [search, retrieve, answer])

        expected_retrieval = (call.get_message(latest.id) if retrieval_tool == "get_email"
                              else call.get_thread(latest.thread_id))
        self.assertEqual(self.gmail.mock_calls, [
            call.search_messages(query="from:jane@example.com", max_results=1),
            expected_retrieval,
        ])
        self.assertEqual([m.name for m in messages if isinstance(m, ToolMessage)], [
            "search_emails", retrieval_tool,
        ])
        self.assertIn(latest.body, messages[-1].content)

    def test_latest_email_searches_then_gets_email_before_answering(self):
        self.latest_email_scenario("get_email")

    def test_latest_email_can_retrieve_thread_before_answering(self):
        self.latest_email_scenario("get_email_thread")

    def test_conversation_searches_then_reads_entire_thread_before_summary(self):
        prompt = "Summarize the conversation with Jane about coffee."
        thread = [
            email("coffee-invite", "Would you like coffee on Tuesday?"),
            email("coffee-confirmation", "Tuesday at 10 works. See you at Central Cafe.", day=2),
        ]
        self.gmail.search_messages.return_value = [
            {"id": thread[-1].id, "threadId": thread[-1].thread_id},
        ]
        self.gmail.get_thread.return_value = thread

        def search(messages):
            self.assertEqual(messages[-1].content, prompt)
            return tool_call("search_emails", {"query": "from:jane@example.com subject:coffee"}, "search")

        def retrieve(messages):
            found = self.read_tool_result(messages, "search_emails", "search")
            self.gmail.get_thread.assert_not_called()
            return tool_call("get_email_thread", {"thread_id": found[0]["threadId"]}, "thread")

        def summarize(messages):
            fetched = self.read_tool_result(messages, "get_email_thread", "thread")
            self.assertEqual([m["id"] for m in fetched], [m.id for m in thread])
            self.assertEqual([m["body"] for m in fetched], [m.body for m in thread])
            return AIMessage(content="Conversation: " + " ".join(m["body"] for m in fetched))

        messages = self.run_agent(prompt, [search, retrieve, summarize])

        self.assertEqual(self.gmail.mock_calls, [
            call.search_messages(query="from:jane@example.com subject:coffee", max_results=10),
            call.get_thread("jane-coffee-thread"),
        ])
        self.assertEqual([m.name for m in messages if isinstance(m, ToolMessage)], [
            "search_emails", "get_email_thread",
        ])
        for message in thread:
            self.assertIn(message.body, messages[-1].content)

    def test_empty_search_can_answer_without_fetching_a_message(self):
        self.gmail.search_messages.return_value = []

        def search(messages):
            return tool_call("search_emails", {"query": "from:jane@example.com"}, "search")

        def answer(messages):
            self.assertEqual(self.read_tool_result(messages, "search_emails", "search"), [])
            return AIMessage(content="I couldn't find any emails from Jane.")

        messages = self.run_agent("What is my latest email from Jane about?", [search, answer])

        self.assertEqual(self.gmail.mock_calls, [
            call.search_messages(query="from:jane@example.com", max_results=10),
        ])
        self.assertEqual(messages[-1].content, "I couldn't find any emails from Jane.")


if __name__ == "__main__":
    unittest.main()
