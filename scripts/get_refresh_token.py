from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

if Path("credentials.json").exists():
    print("Found credentials.json file.")
else:
    print("credentials.json file not found. Please make sure it is in the same directory as this script.")
    exit(1)

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\nAdd these three lines to your .env file:\n")
print(f"GOOGLE_CLIENT_ID={creds.client_id}")
print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")


