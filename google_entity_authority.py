"""

Measure Google Knowledge Graph / entity resolution signals for a set of
queries using Google SERP data returned by Serp Api (serpapi.com).

"""

import argparse
import csv
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv
from serpapi import GoogleSearch


# Environment / defaults

load_dotenv()

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
if not SERPAPI_API_KEY:
    raise ValueError("Missing SERPAPI_API_KEY (set it in your .env file)")

BASE_DIR = "/Benchmarking"

DEFAULT_INPUT_CSV = os.path.join(BASE_DIR, "input", "entity_queries.csv")
DEFAULT_RESULTS_DIR = os.path.join(
    BASE_DIR, "results", "entity_authority"
)

TARGET_DOMAIN = "example.com"

QUERIES_PER_HOUR = 200
SECONDS_BETWEEN = 3600 / QUERIES_PER_HOUR

MAX_RETRIES = 3
RETRY_BACKOFF = 5


# Output fields

FIELDNAMES = [
    "Entity",
    "Query",
    "Expected Website",
    "KG Present",
    "KG MID",
    "KG Title",
    "KG Type",
    "KG Entity Type",
    "KG Description",
    "KG Website",
    "KG Canonical",
    "KG Wikidata",
    "KG Wikipedia",
    "KG Image",
    "KG Place ID",
    "KG Search Link",
    "KG Source",
    "KG Source Link",
    "KG Profile Links",
    "KG Profile Names",
    "KG Raw JSON",
    "Target Website Present",
    "Target Website Rank",
    "Expected URL Present",
    "Expected URL Rank",
    "Top 20 URLs",
    "Top 20 Sources",
    "Target URLs in Top 20",
    "Search Date",
]

CLUSTER_FIELDS = [
    "KG MID",
    "KG Title",
    "KG Type",
    "KG Entity Type",
    "KG Website",
    "KG Place ID",
    "KG Source",
    "Query Count",
    "Entities",
    "Queries",
]


# Helpers

def normalize_url(url):
    """Normalize a URL for matching."""
    if not url:
        return ""

    url = str(url).strip()
    parsed = urlparse(
        url if "://" in url else "https://" + url
    )

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path.rstrip("/").lower()
    return netloc + path


def domain_in_url(url, domain):
    """Return True when a URL belongs to the supplied domain."""
    if not url or not domain:
        return False

    normalized = normalize_url(url)
    target = domain.lower().strip()

    if target.startswith("www."):
        target = target[4:]

    return (
        normalized == target
        or normalized.startswith(target + "/")
    )


def url_matches_expected(link, expected):
    """
    True when `link` is the expected URL or a child path of it.
    """
    if not expected:
        return False

    n_link = normalize_url(link)
    n_expected = normalize_url(expected)

    if not n_link or not n_expected:
        return False

    return (
        n_link == n_expected
        or n_link.startswith(n_expected + "/")
    )


def safe_filename(value):
    """Create a filesystem-safe filename from a query."""
    value = re.sub(r"[^\w\s.-]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "_", value.strip())
    return value[:150] or "query"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def first_nonempty(*values):
    """Return the first non-empty value."""
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


# Input

def read_queries(path):
    """
    Read entity query CSV.

    Required columns:
        Entity, Query, Expected Website

    Extra columns are ignored.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        required = {"Entity", "Query", "Expected Website"}
        missing = required - set(reader.fieldnames or [])

        if missing:
            raise ValueError(
                f"Input CSV is missing required columns: "
                f"{', '.join(sorted(missing))}"
            )

        rows = []

        for row in reader:
            query = (row.get("Query") or "").strip()

            if not query:
                continue

            rows.append({
                "Entity": (row.get("Entity") or "").strip(),
                "Query": query,
                "Expected Website": (
                    row.get("Expected Website") or ""
                ).strip(),
            })

    return rows


def load_done_queries(path):
    """Return queries already present in an output CSV."""
    if not os.path.exists(path):
        return set()

    done = set()

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            query = (row.get("Query") or "").strip()
            if query:
                done.add(query)

    return done


# SERP

def get_serp(query):
    """Run one Google search through SerpApi with retries."""
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            search = GoogleSearch({
                "q": query,
                "engine": "google",
                "google_domain": "google.com",
                "gl": "us",
                "hl": "en",
                "api_key": SERPAPI_API_KEY,
            })

            data = search.get_dict()

            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"])

            return data

        except Exception as exc:
            last_error = exc
            print(
                f"  ! SERP attempt {attempt}/{MAX_RETRIES} failed: "
                f"{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)

    raise RuntimeError(
        f"SERP failed after {MAX_RETRIES} attempts: {last_error}"
    )


# Knowledge Graph extraction

def extract_kg(data):
    """
    Extract Knowledge Graph fields.

    KG MID is deliberately treated as the primary entity identifier.
    No lexical "title matches query" field is calculated because Google's
    KG title can legitimately differ from the query while referring to the
    same entity.
    """
    kg = data.get("knowledge_graph") or {}

    if not isinstance(kg, dict):
        kg = {}

    profiles = kg.get("profiles") or []

    if not isinstance(profiles, list):
        profiles = []

    profile_links = []
    profile_names = []

    for profile in profiles:
        if not isinstance(profile, dict):
            continue

        link = profile.get("link") or ""
        name = profile.get("name") or ""

        if link:
            profile_links.append(str(link))

        if name:
            profile_names.append(str(name))

    source = kg.get("source") or {}

    if not isinstance(source, dict):
        source = {}

    # Preserve the complete KG object as JSON in the CSV. The raw SERP is
    # also saved separately, so this is convenient for spreadsheet analysis.
    raw_kg = json.dumps(
        kg,
        ensure_ascii=False,
        separators=(",", ":"),
    ) if kg else ""

    return {
        "KG Present": "Yes" if kg else "No",
        "KG MID": kg.get("kgmid", ""),
        "KG Title": kg.get("title", ""),
        "KG Type": kg.get("type", ""),
        "KG Entity Type": kg.get("entity_type", ""),
        "KG Description": kg.get("description", ""),
        "KG Website": kg.get("website", ""),
        "KG Canonical": first_nonempty(
            kg.get("canonical"),
            kg.get("canonical_url"),
        ),
        "KG Wikidata": first_nonempty(
            kg.get("wikidata"),
            kg.get("wikidata_id"),
        ),
        "KG Wikipedia": first_nonempty(
            kg.get("wikipedia"),
            kg.get("wikipedia_url"),
        ),
        "KG Image": kg.get("image", ""),
        "KG Place ID": kg.get("place_id", ""),
        "KG Search Link": first_nonempty(
            kg.get("knowledge_graph_search_link"),
            kg.get("kg_search_link"),
        ),
        "KG Source": source.get("name", ""),
        "KG Source Link": source.get("link", ""),
        "KG Profile Links": " | ".join(profile_links),
        "KG Profile Names": " | ".join(profile_names),
        "KG Raw JSON": raw_kg,
    }


# Organic extraction

def extract_organic(data):
    """Extract the top 20 organic URLs and source names."""
    organic = data.get("organic_results") or []

    top_urls = []
    top_sources = []

    for result in organic[:20]:
        if not isinstance(result, dict):
            continue

        link = result.get("link") or ""
        source = result.get("source") or ""

        if link:
            top_urls.append(link)

        if source:
            top_sources.append(source)

    return {
        "Top 20 URLs": " | ".join(top_urls),
        "Top 20 Sources": " | ".join(top_sources),
    }


def extract_ranks(data, expected_url):
    """
    Find the first target-domain result and first expected-URL result.

    Uses SerpApi's explicit position when available.
    """
    organic = data.get("organic_results") or []

    target_rank = ""
    expected_rank = ""
    target_urls = []

    for idx, result in enumerate(organic, start=1):
        if not isinstance(result, dict):
            continue

        link = result.get("link") or ""
        position = result.get("position", idx)

        if domain_in_url(link, TARGET_DOMAIN):
            target_urls.append(link)

            if target_rank == "":
                target_rank = position

        if expected_rank == "" and url_matches_expected(
            link, expected_url
        ):
            expected_rank = position

    return {
        "Target Website Present": (
            "Yes" if target_rank != "" else "No"
        ),
        "Target Website Rank": target_rank,
        "Expected URL Present": (
            "Yes" if expected_rank != "" else "No"
        ),
        "Expected URL Rank": expected_rank,
        "Target URLs in Top 20": " | ".join(target_urls),
    }

# Entity clustering

def build_clusters(rows):
    """
    Group query results by KG MID.

    This is the core entity-resolution output:
        MID -> queries that Google resolved to that entity.

    Rows without a MID are excluded from the MID clusters because there is no
    entity identifier to group them by.
    """
    clusters = defaultdict(list)

    for row in rows:
        mid = (row.get("KG MID") or "").strip()

        if mid:
            clusters[mid].append(row)

    output = []

    for mid, members in sorted(clusters.items()):
        first = members[0]

        entities = sorted({
            m.get("Entity", "").strip()
            for m in members
            if m.get("Entity", "").strip()
        })

        queries = [
            m.get("Query", "").strip()
            for m in members
            if m.get("Query", "").strip()
        ]

        output.append({
            "KG MID": mid,
            "KG Title": first.get("KG Title", ""),
            "KG Type": first.get("KG Type", ""),
            "KG Entity Type": first.get("KG Entity Type", ""),
            "KG Website": first.get("KG Website", ""),
            "KG Place ID": first.get("KG Place ID", ""),
            "KG Source": first.get("KG Source", ""),
            "Query Count": len(members),
            "Entities": " | ".join(entities),
            "Queries": " | ".join(queries),
        })

    return output


def write_clusters(rows, path):
    """Write the MID/entity-cluster report."""
    clusters = build_clusters(rows)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CLUSTER_FIELDS,
        )
        writer.writeheader()
        writer.writerows(clusters)

    return clusters

# Main

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Measure Google Knowledge Graph/entity resolution "
            "using SerpApi SERP data."
        )
    )

    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_CSV,
        help="Path to entity_queries.csv",
    )

    parser.add_argument(
        "--results-dir",
        default=DEFAULT_RESULTS_DIR,
        help="Output directory",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N queries (0 = all)",
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing results and start fresh",
    )

    args = parser.parse_args()

    input_csv = args.input
    results_dir = args.results_dir
    raw_dir = os.path.join(results_dir, "raw_json")

    output_csv = os.path.join(
        results_dir,
        "entity_authority_results.csv",
    )

    cluster_csv = os.path.join(
        results_dir,
        "entity_clusters_by_mid.csv",
    )

    os.makedirs(raw_dir, exist_ok=True)

    queries = read_queries(input_csv)

    if args.limit:
        queries = queries[:args.limit]

    print(f"Loaded {len(queries)} queries from {input_csv}")

    resume = not args.no_resume
    done = (
        load_done_queries(output_csv)
        if resume
        else set()
    )

    if done:
        print(
            f"Resume: {len(done)} queries already recorded; "
            "they will be skipped."
        )

    # If resuming, load the existing rows so the cluster report includes
    # both old and newly collected results.
    existing_rows = []

    if resume and os.path.exists(output_csv):
        with open(
            output_csv,
            newline="",
            encoding="utf-8",
        ) as f:
            existing_rows = list(csv.DictReader(f))

    file_mode = "a" if (resume and os.path.exists(output_csv)) else "w"

    collected_rows = list(existing_rows)

    with open(
        output_csv,
        file_mode,
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=FIELDNAMES,
        )

        if file_mode == "w":
            writer.writeheader()

        for i, item in enumerate(queries, start=1):
            entity = item["Entity"]
            query = item["Query"]
            expected_website = item["Expected Website"]

            if query in done:
                print(
                    f"[{i}/{len(queries)}] SKIP (done): {query}"
                )
                continue

            print(f"[{i}/{len(queries)}] {query}")

            try:
                serp = get_serp(query)

                # Save complete raw SERP JSON.
                raw_path = os.path.join(
                    raw_dir,
                    safe_filename(query) + ".json",
                )

                with open(
                    raw_path,
                    "w",
                    encoding="utf-8",
                ) as jf:
                    json.dump(
                        serp,
                        jf,
                        indent=2,
                        ensure_ascii=False,
                    )

                kg = extract_kg(serp)
                organic = extract_organic(serp)
                ranks = extract_ranks(
                    serp,
                    expected_website,
                )

                row = {
                    "Entity": entity,
                    "Query": query,
                    "Expected Website": expected_website,
                    **kg,
                    **ranks,
                    **organic,
                    "Search Date": now_iso(),
                }

                writer.writerow(row)
                f.flush()

                collected_rows.append(row)

                mid = row["KG MID"]

                if mid:
                    print(
                        f"  KG MID: {mid} | "
                        f"Title: {row['KG Title']}"
                    )
                else:
                    print("  KG MID: none")

            except Exception as exc:
                print(
                    f"  ! Error on '{query}': {exc}"
                )

            # One SERP request per query.
            if i < len(queries):
                time.sleep(SECONDS_BETWEEN)

    clusters = write_clusters(
        collected_rows,
        cluster_csv,
    )

    print("\nDone.")
    print(f"  Results:  {output_csv}")
    print(f"  Clusters: {cluster_csv}")
    print(f"  Raw JSON: {raw_dir}")
    print(
        f"  Distinct MIDs observed: {len(clusters)}"
    )


if __name__ == "__main__":
    main()
