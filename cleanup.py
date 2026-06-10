import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]

headers = {"Authorization": f"Bearer {BOT_TOKEN}"}

response = requests.get(
    "https://slack.com/api/conversations.history",
    headers=headers,
    params={"channel": CHANNEL_ID, "limit": 200}
)

data = response.json()
print(f"API response ok: {data.get('ok')} {data.get('error', '')}")

messages = data.get("messages", [])
print(f"Found {len(messages)} messages")

for msg in messages:
    ts = msg.get("ts")
    del_response = requests.post(
        "https://slack.com/api/chat.delete",
        headers=headers,
        json={"channel": CHANNEL_ID, "ts": ts}
    )
    result = del_response.json()
    print(f"Deleted {ts}: {result.get('ok')} {result.get('error', '')}")
    time.sleep(1.2)  # chat.delete is rate-limited (~50/min); avoids rate_limited errors