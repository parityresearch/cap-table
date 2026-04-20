import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

URLS = [
    {
        "source": "Independent",
        "url": "https://www.independent.co.uk/news/world/middle-east/iran-us-war-live-trump-ceasefire-peace-talks-pakistan-b2955715.html",
    },
    {
        "source": "NBC News",
        "url": "https://www.nbcnews.com/world/iran/live-blog/live-updates-trump-iran-hormuz-israel-lebanon-ceasefire-talks-pakistan-rcna285140",
    },
    {
        "source": "PBS NewsHour",
        "url": "https://www.pbs.org/newshour/world/en-route-to-war-negotiations-vance-warns-iran-not-to-play-the-u-s",
    },
    {
        "source": "NDTV",
        "url": "https://www.ndtv.com/world-news/jd-vances-full-statement-on-failed-us-iran-talks-in-islamabad-we-leave-with-final-best-offer-11345265",
    },
    {
        "source": "USA Today",
        "url": "https://www.usatoday.com/story/news/world/2026/04/11/trump-vance-iran-war-pakistan-peace-talks-live/89561515007/",
    },
]

SENTIMENT_LEXICON = {
    "good": 2,
    "better": 2,
    "best": 3,
    "positive": 2,
    "encouraging": 2,
    "hope": 1,
    "hopeful": 2,
    "optimistic": 2,
    "agree": 1,
    "agreed": 1,
    "agreeing": 1,
    "successful": 2,
    "success": 2,
    "win": 2,
    "wins": 2,
    "strong": 1,
    "support": 1,
    "supportive": 1,
    "safe": 1,
    "productive": 2,
    "bad": -2,
    "worse": -2,
    "worst": -3,
    "negative": -2,
    "failure": -3,
    "failed": -3,
    "failure": -3,
    "missed": -1,
    "deadlock": -2,
    "stalemate": -2,
    "hostile": -2,
    "dangerous": -2,
    "reject": -2,
    "rejected": -2,
    "denied": -2,
    "unauthorized": -2,
    "no deal": -3,
    "no agreement": -3,
    "tough": -1,
    "bad news": -2,
    "worry": -2,
    "concern": -1,
    "concerns": -1,
    "risk": -1,
    "risks": -1,
    "hard": -1,
    "problem": -2,
    "problems": -2,
    "failed": -3,
    "failure": -3,
    "failing": -3,
    "pain": -2,
    "pressure": -1,
    "fierce": -1,
    "angry": -2,
    "anger": -2,
    "critic": -1,
    "criticize": -1,
    "criticized": -1,
    "criticism": -1,
    "unacceptable": -2,
    "unlikely": -1,
    "collapse": -2,
    "breakdown": -3,
    "difficult": -1,
    "difficulty": -1,
    "complicated": -1,
    "problematic": -2,
    "frustrated": -2,
    "frustration": -2,
    "disappointing": -2,
}


def fetch_html(url: str, timeout: int = 20) -> str:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "form"]):
        tag.extract()

    article_texts = []
    article_tags = soup.select("article, .article-body, .story-content, .content, .article__body, .article-body-content")
    if article_tags:
        for tag in article_tags:
            article_texts.extend([p.get_text(" ", strip=True) for p in tag.find_all("p") if p.get_text(strip=True)])
    if not article_texts:
        article_texts = [p.get_text(" ", strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    return "\n\n".join(article_texts)


def get_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string if soup.title else ""
    if title:
        return title.strip()
    og_title = soup.select_one('meta[property="og:title"]')
    if og_title and og_title.get("content"):
        return og_title["content"].strip()
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9'\s]+", " ", text.lower())


def compute_sentiment(text: str) -> tuple[float, int, int]:
    normalized = normalize_text(text)
    tokens = normalized.split()
    total = len(tokens)
    if total == 0:
        return 0.0, 0, 0

    score = 0
    pos_count = 0
    neg_count = 0
    for token in tokens:
        token_score = SENTIMENT_LEXICON.get(token, 0)
        if token_score > 0:
            pos_count += 1
        elif token_score < 0:
            neg_count += 1
        score += token_score

    normalized_score = score / total * 100
    return round(normalized_score, 3), pos_count, neg_count


def save_chart(df: pd.DataFrame, output_path: Path) -> None:
    plt.figure(figsize=(10, 5))
    bars = plt.barh(df["source"], df["sentiment_score"], color=["#d62728" if x < 0 else "#1f77b4" for x in df["sentiment_score"]])
    plt.axvline(0, color="#444444", linewidth=0.8)
    plt.xlabel("Sentiment score")
    plt.title("Estimated public reaction sentiment for JD Vance Iran talks")
    for bar, score in zip(bars, df["sentiment_score"]):
        plt.text(bar.get_width() + (0.5 if score >= 0 else -0.5), bar.get_y() + bar.get_height() / 2,
                 f"{score:.2f}", va="center",
                 ha="left" if score >= 0 else "right",
                 color="#000000")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch recent online articles about JD Vance Iran talks and estimate reaction sentiment."
    )
    parser.add_argument(
        "--output-csv",
        default=OUTPUT_DIR / "reaction_sentiment.csv",
        help="Path to write the sentiment CSV file.",
    )
    parser.add_argument(
        "--output-png",
        default=OUTPUT_DIR / "reaction_sentiment.png",
        help="Path to write the sentiment chart PNG file.",
    )
    args = parser.parse_args()

    rows = []
    for item in URLS:
        url = item["url"]
        print(f"Fetching {item['source']}...")
        try:
            html = fetch_html(url)
        except Exception as exc:
            print(f"Warning: could not fetch {item['source']} ({url}): {exc}")
            continue

        title = get_title(html)
        text = extract_text_from_html(html)
        sentiment_score, positive_count, negative_count = compute_sentiment(text)
        excerpt = " ".join(text.split()[:60])
        rows.append(
            {
                "source": item["source"],
                "url": url,
                "title": title,
                "sentiment_score": sentiment_score,
                "positive_count": positive_count,
                "negative_count": negative_count,
                "excerpt": excerpt,
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(args.output_csv, index=False)
    save_chart(df, Path(args.output_png))
    print(f"Wrote sentiment CSV to {args.output_csv}")
    print(f"Wrote sentiment chart to {args.output_png}")


if __name__ == "__main__":
    main()
