from dotenv import load_dotenv
from dataclasses import dataclass, asdict
from gmail.client import GmailClient
from langchain.agents import AgentState, create_agent
from langchain.tools import tool, ToolRuntime
from langgraph.types import Command
from langchain.messages import ToolMessage
from langchain.agents.middleware import wrap_model_call, dynamic_prompt, HumanInTheLoopMiddleware
from langchain.agents.middleware import ModelRequest, ModelResponse
from typing import Callable

load_dotenv()


@dataclass
class EmailContext:
    gmail_client: GmailClient


class AuthenticatedState(AgentState):
    authenticated: bool


def serialize_message(message):
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "sender_name": message.sender_name,
        "sender_email": message.sender_email,
        "recipient": message.recipient,
        "subject": message.subject,
        "body": message.body,
        "date": message.timestamp.isoformat(),
    }


@tool
def search_emails(
    query: str, 
    runtime: ToolRuntime[EmailContext],
    limit: int = 10,
) -> list[dict]:
    """Search the user's Gmail mailbox using Gmail search syntax.
    
    Use this when you need to find emails matching criteria such as
    sender, recipient, subject, unread status, or date.
    
    Examples:
    - "is:unread"
    - "from:alice@example.com"
    - "subject:Meeting newer_than:7d"
    """

    gmail = runtime.context.gmail_client

    refs = gmail.search_messages(
        query=query,
        max_results=limit,
    )

    return refs

@tool
def get_email(
    message_id: str,
    runtime: ToolRuntime[EmailContext],
) -> dict:
    """
    Retrieve the full contents and metadata of a Gmail message by its ID.

    Use this after search_emails when you need to read a specific email.
    """
    gmail = runtime.context.gmail_client

    message = gmail.get_message(message_id)

    return serialize_message(message)

@tool
def get_email_thread(
    thread_id: str,
    runtime: ToolRuntime[EmailContext],
) -> dict:
    """
    Retrieve all messages in an email conversation in chronological order.

    Use this when understanding the full conversation is important,
    such as summarizing a thread or drafting a contextual reply.
    """

    gmail = runtime.context.gmail_client

    messages = gmail.get_thread(thread_id)

    return [serialize_message(message) for message in messages]


# @tool
# def authenticate(email: str, password: str, runtime: ToolRuntime) -> Command:
#     """Authenticate the user with the given email and password"""
#     if email == runtime.context.email_address and password == runtime.context.password:
#         return Command(
#             update={
#                 "authenticated": True,
#                 "messages": [
#                     ToolMessage("Successfully authenticated", tool_call_id=runtime.tool_call_id)
#                 ],
#             }
#         )
#     else:
#         return Command(
#             update={
#                 "authenticated": False,
#                 "messages": [
#                     ToolMessage("Authentication failed", tool_call_id=runtime.tool_call_id)
#                 ],
#             }
#         )


# @wrap_model_call
# async def dynamic_tool_call(
#     request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
# ) -> ModelResponse:
#     """Allow read inbox and send email tools only if user provides correct email and password"""

#     authenticated = request.state.get("authenticated")

#     if authenticated:
#         tools = [check_inbox, send_email]
#     else:
#         tools = [authenticate]

#     request = request.override(tools=tools)
#     return await handler(request)

    # user_id = request.context.user_id
    # gmail_connected = credential_store.has_google_credentials(user_id)

    # if gmail_connected:
    #     tools = [
    #         search_emails,
    #         get_email,
    #         create_email_draft,
    #     ]
    # else:
    #     tools = [
    #         connect_gmail,
    #      ]

    # return await handler(
    #     request.override(tools=tools)
    # )




# authenticated_prompt = "You are a helpful assistant that can check the inbox and send emails."
# unauthenticated_prompt = "You are a helpful assistant that can authenticate users."


# @dynamic_prompt
# def dynamic_prompt_func(request: ModelRequest) -> str:
#     """Generate system prompt based on authentication status"""
#     authenticated = request.state.get("authenticated")

#     if authenticated:
#         return authenticated_prompt
#     else:
#         return unauthenticated_prompt


email_agent = create_agent(
        "gpt-5-nano",
        tools=[search_emails, get_email, get_email_thread],
        context_schema=EmailContext,
    )
