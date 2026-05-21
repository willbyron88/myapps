import os
import requests
from dotenv import load_dotenv

load_dotenv()

FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")

GRAPH_VERSION = "v25.0"


def get_json(url, params, label="", raise_on_error=True):
    """
    Helper for calling Meta Graph API.
    Prints status and response preview.
    If raise_on_error=False, it returns None instead of crashing.
    """
    response = requests.get(url, params=params, timeout=30)

    print("\n" + "=" * 80)
    print(label or url)
    print("=" * 80)
    print("Status:", response.status_code)
    print(response.text[:2000])

    if raise_on_error:
        response.raise_for_status()
    elif response.status_code >= 400:
        return None

    return response.json()


def main():
    if not FB_PAGE_ID:
        raise RuntimeError("Missing FB_PAGE_ID in .env")

    if not FB_PAGE_ACCESS_TOKEN:
        raise RuntimeError("Missing FB_PAGE_ACCESS_TOKEN in .env")

    # ------------------------------------------------------------
    # 1. Test Page basic info
    # ------------------------------------------------------------
    print("\nTesting Facebook Page access...")

    page_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}"

    page_data = get_json(
        page_url,
        {
            "fields": "id,name,fan_count,followers_count",
            "access_token": FB_PAGE_ACCESS_TOKEN,
        },
        label="PAGE BASIC INFO",
    )

    print("\nPage info:")
    print(page_data)

    # ------------------------------------------------------------
    # 2. Get recent posts
    # ------------------------------------------------------------
    posts_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/posts"

    posts_data = get_json(
        posts_url,
        {
            "fields": "id,message,created_time,permalink_url",
            "limit": 5,
            "access_token": FB_PAGE_ACCESS_TOKEN,
        },
        label="RECENT POSTS",
    )

    posts = posts_data.get("data", [])

    if not posts:
        print("\nNo posts found.")
        return

    print("\nRecent posts found:")
    for post in posts:
        print("-" * 80)
        print("ID:", post.get("id"))
        print("Created:", post.get("created_time"))
        print("Message:", (post.get("message") or "")[:160])
        print("URL:", post.get("permalink_url"))

    # Use first post for deeper tests
    first_post = posts[0]
    post_id = first_post["id"]

    print("\nTesting first post:")
    print("Post ID:", post_id)
    print("Post URL:", first_post.get("permalink_url"))

    # ------------------------------------------------------------
    # 3. Test basic post fields only
    # ------------------------------------------------------------
    post_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{post_id}"

    basic_post_data = get_json(
        post_url,
        {
            "fields": "id,created_time,message,permalink_url",
            "access_token": FB_PAGE_ACCESS_TOKEN,
        },
        label="BASIC POST FIELDS",
    )

    print("\nBasic post summary:")
    print(basic_post_data)

    # ------------------------------------------------------------
    # 4. Test post insights
    # ------------------------------------------------------------
    insights_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{post_id}/insights"

    insights_data = get_json(
        insights_url,
        {
            "metric": (
                "post_impressions,"
                "post_impressions_unique,"
                "post_engaged_users"
            ),
            "access_token": FB_PAGE_ACCESS_TOKEN,
        },
        label="POST INSIGHTS",
        raise_on_error=False,
    )

    if insights_data:
        print("\nPost insights parsed:")
        parsed = {}

        for item in insights_data.get("data", []):
            name = item.get("name")
            values = item.get("values", [])
            value = values[-1].get("value", 0) if values else 0
            parsed[name] = value

        print(parsed)
    else:
        print("\nPost insights failed.")
        print("This likely means the Page token does not include read_insights,")
        print("or Meta is not exposing insights for this post yet.")

    # ------------------------------------------------------------
    # 5. Optional engagement fields
    # ------------------------------------------------------------
    print("\nTesting optional engagement fields...")
    print("This may fail if Meta blocks reactions/comments/shares for the token.")

    engagement_data = get_json(
        post_url,
        {
            "fields": (
                "id,"
                "shares,"
                "comments.summary(true),"
                "reactions.summary(true)"
            ),
            "access_token": FB_PAGE_ACCESS_TOKEN,
        },
        label="OPTIONAL ENGAGEMENT FIELDS",
        raise_on_error=False,
    )

    if engagement_data:
        print("\nOptional engagement parsed:")
        print(engagement_data)
    else:
        print("\nOptional engagement fields failed, but that is okay for now.")
        print("We can still integrate Facebook using posts + insights if insights works.")


if __name__ == "__main__":
    main()