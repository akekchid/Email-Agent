import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]


def get_credentials():
    """ Obtain Gmail API credentials, refreshing or generating them if necessary. """
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file(
            "token.json",
            SCOPES,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES,
            )

            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return creds


def main():
    """ Main function to test Gmail API integration by fetching and displaying recent messages. """
    creds = get_credentials()

    try:
        service = build(
            "gmail",
            "v1",
            credentials=creds,
        )

        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                labelIds=["INBOX"],
                maxResults=5,
            )
            .execute()
        )

        messages = result.get("messages", [])

        if not messages:
            print("No messages found.")
            return

        print(f"Found {len(messages)} messages:\n")

        for message in messages:
            message_id = message["id"]

            full_message = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=[
                        "From",
                        "To",
                        "Subject",
                        "Date",
                    ],
                )
                .execute()
            )

            headers = full_message["payload"].get(
                "headers",
                []
            )

            header_map = {
                header["name"]: header["value"]
                for header in headers
            }

            print("FROM:", header_map.get("From"))
            print("SUBJECT:", header_map.get("Subject"))
            print("DATE:", header_map.get("Date"))
            print("SNIPPET:", full_message.get("snippet"))
            print("-" * 60)

    except HttpError as error:
        print(f"Gmail API error: {error}")


if __name__ == "__main__":
    main()