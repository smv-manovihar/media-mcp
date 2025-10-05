from __future__ import annotations
import time
import traceback
import requests
import nltk
import re
import math
from datetime import datetime
from bs4 import BeautifulSoup
from ddgs import DDGS
from urllib.parse import urlparse
from typing import Dict, Any, Generator, List, Tuple
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("web_search_scraper", port=8001)


# --- NLTK Setup ---
try:
    nltk.data.find("tokenizers/punkt")
except Exception:
    print("First-time setup: Downloading NLTK 'punkt' model...")
    nltk.download("punkt")


try:
    nltk.data.find("corpora/stopwords")
except Exception:
    nltk.download("stopwords")


from nltk.corpus import stopwords


# --- Configuration ---
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; MyWebScraper/1.0; +http://mywebsite.com/bot)"
)
DEFAULT_TIMEOUT = 10
MIN_DELAY_BETWEEN_REQUESTS = 1.0

# Increased limits to provide more details while avoiding excessive context bloat
MAX_EXTRACT_CHARS = 3000  # Increased from 1000
MAX_ANSWER_CHARS = 2000  # Increased from 800
MAX_SENTENCES = 12  # Increased from 6
MAX_EXCERPT_PER_SOURCE = 300  # Increased from 150


# --- State Management ---
_domain_last_request: Dict[str, float] = {}


# --- Helper Functions ---
def _get_domain(url: str) -> str | None:
    """Extracts the network location (domain) from a URL."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return None


def _enforce_request_delay(url: str) -> None:
    """Waits if necessary to respect MIN_DELAY_BETWEEN_REQUESTS for a domain."""
    domain = _get_domain(url)
    if not domain:
        return

    now = time.time()
    last_request_time = _domain_last_request.get(domain, 0)
    elapsed = now - last_request_time

    if elapsed < MIN_DELAY_BETWEEN_REQUESTS:
        time.sleep(MIN_DELAY_BETWEEN_REQUESTS - elapsed)

    _domain_last_request[domain] = time.time()


def _tokenize_words(text: str) -> List[str]:
    """Tokenize text, removing stopwords."""
    tokens = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text.lower())
    return [t for t in tokens if t and t not in STOPWORDS]


STOPWORDS = set(stopwords.words("english"))


# --- Agent Tools ---
@mcp.tool("current_datetime")
def current_datetime():
    """Get the current date and time."""
    return {"date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def search_web(query: str, max_results: int = 10) -> Dict[str, Any]:
    """Performs a web search using DuckDuckGo."""
    print(f"-> Searching for: '{query}'")
    try:
        results_generator: Generator[Dict[str, str], None, None] = DDGS(
            timeout=DEFAULT_TIMEOUT
        ).text(query=query, max_results=max_results)
        results = [
            {
                "title": r.get("title", ""),
                "href": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in results_generator
        ]
        return {"results": results}
    except Exception as e:
        return {
            "error": f"search_web failed: {str(e)}",
            "trace": traceback.format_exc(),
        }


@mcp.tool("extract_relevant_content")
def extract_relevant_content(
    url: str, query: str, max_chars: int = MAX_EXTRACT_CHARS
) -> Dict[str, Any]:
    """
    Scrapes a webpage and extracts the most relevant sentences based on a query.
    Optimized to reduce memory footprint.
    """
    print(f"-> Extracting content from '{url}'")
    try:
        _enforce_request_delay(url)
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove non-content tags
        tags_to_remove = [
            "script",
            "style",
            "header",
            "footer",
            "nav",
            "aside",
            "form",
            "a",
        ]
        for element in soup(tags_to_remove):
            element.decompose()

        full_text = " ".join(soup.stripped_strings)
        if not full_text:
            return {"url": url, "content": "No text content found."}

        # Early truncation to save memory
        if len(full_text) > max_chars * 3:
            full_text = full_text[: max_chars * 3]

        sentences = nltk.sent_tokenize(full_text)
        query_words = set(word.lower() for word in query.split())

        # Score only promising sentences
        scored_sentences = []
        for i, sentence in enumerate(sentences):
            if len(sentence) < 20:  # Skip very short sentences
                continue
            sentence_words = set(word.lower() for word in nltk.word_tokenize(sentence))
            score = len(query_words.intersection(sentence_words))
            if score > 0:
                scored_sentences.append((score, i, sentence))
                if len(scored_sentences) >= 40:  # Increased candidates from 20

                    break

        if not scored_sentences:
            return {"url": url, "content": full_text[:max_chars]}

        scored_sentences.sort(key=lambda x: x[0], reverse=True)

        # Include top scored + context
        final_sentences = {}
        for score, index, sentence in scored_sentences[:16]:  # Increased from 8
            final_sentences[index] = sentence
            if index > 0 and (index - 1) not in final_sentences:
                final_sentences[index - 1] = sentences[index - 1]
            if index < len(sentences) - 1 and (index + 1) not in final_sentences:
                final_sentences[index + 1] = sentences[index + 1]

        # Build final text within limit
        sorted_indices = sorted(final_sentences.keys())
        final_text = ""
        for index in sorted_indices:
            next_sentence = final_sentences[index]
            if len(final_text) + len(next_sentence) + 1 > max_chars:
                break
            final_text += next_sentence + " "

        return {"url": url, "content": final_text.strip()}

    except requests.exceptions.RequestException as e:
        return {"error": f"Network error: {str(e)}"}
    except Exception as e:
        return {"error": f"Extraction failed: {str(e)}"}


@mcp.tool("web_search")
def web_search(
    query: str,
    max_results: int = 5,  # Increased default from 3
    max_chars: int = MAX_ANSWER_CHARS,
) -> Dict[str, Any]:
    """
    Search the web, scrape top results, and synthesize an extractive answer.
    Optimized to minimize context length.

    Returns:
      {
        "query": query,
        "answer": "<synthesized summary>",
        "sources": [{"title": ..., "href": ..., "excerpt": ...}, ...]
      }
    """
    print(f"-> web_search: '{query}' (max_results={max_results})")
    try:
        search_results = search_web(query=query, max_results=max_results)
        if "error" in search_results:
            return {"error": search_results["error"]}

        results = search_results.get("results", [])
        if not results:
            return {"query": query, "answer": "", "sources": []}

        # Collect sentences from scraped content
        corpus_sentences: List[Tuple[str, str, int, int]] = []
        # Format: (url, sentence_text, sentence_index, result_rank)

        for rank, res in enumerate(results):
            url = res.get("href") or ""
            if not url:
                continue

            extracted = extract_relevant_content(
                url=url, query=query, max_chars=MAX_EXTRACT_CHARS
            )
            if "error" in extracted:
                continue

            excerpt = extracted.get("content", "")
            sentences = nltk.sent_tokenize(excerpt)

            # Limit sentences per source
            for idx, sent in enumerate(sentences[:20]):  # Increased from 10 per source
                if len(sent) > 20:  # Skip short sentences
                    corpus_sentences.append((url, sent, idx, rank))

        if not corpus_sentences:
            return {"query": query, "answer": "", "sources": []}

        # Build IDF for scoring
        N = len(corpus_sentences)
        df: Dict[str, int] = {}
        sentence_tokens: List[List[str]] = []

        for _url, sent, _idx, _rank in corpus_sentences:
            tokens = [
                t
                for t in re.findall(r"[A-Za-z0-9]+", sent.lower())
                if t not in STOPWORDS
            ]
            sentence_tokens.append(tokens)
            unique_tokens = set(tokens)
            for t in unique_tokens:
                df[t] = df.get(t, 0) + 1

        def idf(token: str) -> float:
            return math.log((N + 1) / (1 + df.get(token, 0))) + 1.0

        # Score sentences
        query_tokens = [
            t for t in re.findall(r"[A-Za-z0-9]+", query.lower()) if t not in STOPWORDS
        ]
        query_token_set = set(query_tokens)

        scored: List[Tuple[float, int, str, str, int]] = []
        for i, (url, sent, idx, rank) in enumerate(corpus_sentences):
            tokens = sentence_tokens[i]
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1

            overlap_score = sum(tf.get(qt, 0) * idf(qt) for qt in query_token_set)

            # Bonuses
            pos_bonus = 1.0 / (1 + idx)
            rank_bonus = 1.0 / (1 + rank)
            score = overlap_score * 2.0 + pos_bonus * 0.5 + rank_bonus * 0.5

            scored.append((score, i, url, sent, rank))

        # Select top sentences
        scored.sort(key=lambda x: x[0], reverse=True)
        top_sentences = scored[:MAX_SENTENCES]

        # Group by URL for coherence
        grouped_by_url: Dict[str, List[Tuple[float, int, str, int]]] = {}
        for score, orig_idx, url, sent, rank in top_sentences:
            grouped_by_url.setdefault(url, []).append((score, orig_idx, sent, rank))

        # Order groups by avg score and rank
        groups: List[Tuple[float, int, str, List[str]]] = []
        for url, items in grouped_by_url.items():
            avg_score = sum(it[0] for it in items) / len(items)
            group_rank = min(it[3] for it in items)
            items_sorted = sorted(items, key=lambda x: x[1])
            sentences_ordered = [it[2] for it in items_sorted]
            groups.append((avg_score, group_rank, url, sentences_ordered))

        groups.sort(key=lambda x: (-x[0], x[1]))

        # Build final answer within character limit
        final_text = ""
        used_urls: List[str] = []

        for avg_score, group_rank, url, sents in groups:
            for s in sents:
                if len(final_text) + len(s) + 2 > max_chars:
                    break
                final_text += s.strip() + " "

            if url not in used_urls:
                used_urls.append(url)

            if len(final_text) >= max_chars:
                break

        final_text = final_text.strip()

        # Build compact sources with increased excerpt length
        sources_out = []
        for url in used_urls:
            match = next((r for r in results if r.get("href") == url), None)
            title = match.get("title", url[:50]) if match else url[:50]

            # Use snippet from search results plus a bit more if available
            snippet = match.get("snippet", "")[:MAX_EXCERPT_PER_SOURCE] if match else ""

            sources_out.append(
                {
                    "title": title,
                    "href": url,
                    "excerpt": snippet
                    + ("..." if len(snippet) >= MAX_EXCERPT_PER_SOURCE else ""),
                }
            )

        return {"query": query, "answer": final_text, "sources": sources_out}

    except Exception as e:
        return {"error": f"web_search failed: {str(e)}"}


if __name__ == "__main__":
    try:
        mcp.run(transport="streamable-http")
    except ConnectionResetError:
        pass
    except Exception as e:
        print(f"Server error: {e}")
