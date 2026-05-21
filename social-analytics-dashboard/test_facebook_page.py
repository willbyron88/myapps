import os
import requests
from dotenv import load_dotenv

load_dotenv()

FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
FB_USER_ACCESS_TOKEN = os.getenv("FB_USER_ACCESS_TOKEN")

GRAPH_VERSION = "v25.0"


def get_token():
    if FB_PAGE_ACCESS_TOKEN:
        return FB_PAGE_ACCESS_TOKEN
    if FB_USER_ACCESS_TOKEN:
        return FB_USER_ACCESS_TOKEN
    raise RuntimeError("Missing FB_PAGE_ACCESS_TOKEN or FB_USER_ACCESS_TOKEN in .env")


def get_json(url, params):
    response = requests.get(url, params=params, timeout=30)
    print("Status:", response.status_code)
    print(response.text[:1000])
    response.raise_for_status()
    return response.json()


def main():
    if not FB_PAGE_ID:
        raise RuntimeError("Missing FB_PAGE_ID in .env")

    token = get_token()

    print("\nTesting Page basic info...")
    page_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}"
    page_data = get_json(
        page_url,
        {
            "fields": "id,name,fan_count,followers_count",
            "access_token": token,
        },
    )

    print("\nPage info:")
    print(page_data)

    print("\nTesting recent posts...")
    posts_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/posts"
    posts_data = get_json(
        posts_url,
        {
            "fields": "id,message,created_time,permalink_url",
            "limit": 5,
            "access_token": token,
        },
    )

    print("\nRecent posts:")
    for post in posts_data.get("data", []):
        print("-" * 80)
        print("ID:", post.get("id"))
        print("Created:", post.get("created_time"))
        print("Message:", (post.get("message") or "")[:160])
        print("URL:", post.get("permalink_url"))


if __name__ == "__main__":
    main()