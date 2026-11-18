from uuid import uuid4

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from langgraph.types import Command

from email_agent import EmailContext, email_agent
from gmail.client import GmailClient
from gmail.auth import SCOPES

credentials = Credentials.from_authorized_user_file(
    "token.json", SCOPES)

if credentials.expired and credentials.refresh_token:
    credentials.refresh(Request())

gmail_client = GmailClient(credentials)


config = {
    "configurable": {
        "thread_id": str(uuid4()),
    }
}

drafts = (
    gmail_client.service.users()
    .drafts()
    .list(userId="me", maxResults=5)
    .execute()
    .get("drafts", [])
)

if not drafts:
    raise RuntimeError("No Gmail drafts found.")

for draft in drafts:
    print(f"Draft ID: {draft['id']}")

draft_id = input("Enter the draft ID to send: ").strip()

if not any(draft["id"] == draft_id for draft in drafts):
    raise ValueError(f"Draft ID {draft_id} not found in the list of drafts.")

result = email_agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Use the 'send_email_draft' tool now "
                    f"with draft_id='{draft_id}'. Do not just describe the action."
                ),
            }
        ]
    },
    config=config,
    context=EmailContext(gmail_client=gmail_client),
)

interrupts = result.get("__interrupt__", ())

# print("Agent result:", result)
# print("Messages:", result.get("messages", []))
# print("Interrupts:", interrupts)

assert interrupts, "Expected a human approval interrupt"

for pending in interrupts:
    print(pending.value)

state = email_agent.get_state(config)

print(state.next)
print(state.interrupts)

result = email_agent.invoke(
    Command(
        resume={
            "decisions": [
                {"type": "approve"}
            ]
        }
    ),
    config=config,
    context=EmailContext(gmail_client=gmail_client),
)

print("Approval result:", result)
