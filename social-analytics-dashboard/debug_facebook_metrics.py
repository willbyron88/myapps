import os
import requests
from dotenv import load_dotenv

load_dotenv()

FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
GRAPH_VERSION = "v25.0"

CANDIDATE_METRICS = [
    # Newer/simple candidates
    "post_reactions_by_type_total",
    "post_clicks",
    "post_clicks_by_type",

    # Older/common post insight candidates
    "post_impressions",
    "post_impressions_unique",
    "post_engaged_users",
    "post_negative_feedback",
    "post_video_views",
    "post_video_views_unique",
]


def call(url, params):
    response = requests.get(url, params=params, timeout=30)
    return response.status_code, response.text, response


def main():
    if not FB_PAGE_ID:
        raise RuntimeError("Missing FB_PAGE_ID in .env")
    if not FB_PAGE_ACCESS_TOKEN:
        raise RuntimeError("Missing FB_PAGE_ACCESS_TOKEN in .env")

    print("Getting most recent post...")
    posts_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/posts"
    status, text, response = call(
        posts_url,
        {
            "fields": "id,created_time,permalink_url",
            "limit": 1,
            "access_token": FB_PAGE_ACCESS_TOKEN,
        },
    )

    print("Posts status:", status)
    print(text[:1000])
    response.raise_for_status()

    posts = response.json().get("data", [])
    if not posts:
        print("No posts found.")
        return

    post_id = posts[0]["id"]
    print("\nTesting post:", post_id)
    print("URL:", posts[0].get("permalink_url"))

    insights_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{post_id}/insights"

    working = []
    failing = []

    for metric in CANDIDATE_METRICS:
        print("\n" + "=" * 80)
        print("Testing metric:", metric)

        status, text, response = call(
            insights_url,
            {
                "metric": metric,
                "access_token": FB_PAGE_ACCESS_TOKEN,
            },
        )

        print("Status:", status)
        print(text[:1200])

        if status == 200:
            working.append(metric)
        else:
            failing.append(metric)

    print("\n" + "=" * 80)
    print("WORKING METRICS")
    print("=" * 80)
    for metric in working:
        print(metric)

    print("\n" + "=" * 80)
    print("FAILING METRICS")
    print("=" * 80)
    for metric in failing:
        print(metric)


if __name__ == "__main__":
    main()