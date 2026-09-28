from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


def authenticate_google():
    flow = InstalledAppFlow.from_client_secrets_file(
        'credentials.json',
        SCOPES
    )
    credentials = flow.run_local_server(port=0)

    return credentials