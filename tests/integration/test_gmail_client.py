
from gmail.client import GmailClient
from scripts.test_gmail import get_credentials

def main():
    creds = get_credentials()

    gmail = GmailClient(creds)

    messages = gmail.search_messages(
        query="is:unread",
        max_results=1,
    )

    for message in messages:
        email = gmail.get_message(
            message["id"]
        )

        print("ID:", email.id)
        print("Thread:", email.thread_id)
        print("Sender Name:", email.sender_name)
        print("Sender Email:", email.sender_email)
        print("Recipient:", email.recipient)
        print("Subject:", email.subject)
        print("Timestamp:", email.timestamp)
        print("Body preview:", email.body[:600])

if __name__ == "__main__":
    main()