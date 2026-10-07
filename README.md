# Overview

The central analytical identifier is Google's Knowledge Graph MID (kgmid).
Queries that return the same MID are treated as resolving to the same
Knowledge Graph entity, even when Google's displayed title differs from the
query text.

For each query the script records:
  - Knowledge Graph presence
  - Google Knowledge Graph MID
  - KG title, type, description
  - KG entity_type
  - KG Place ID (when present)
  - KG website/canonical/Wikidata/Wikipedia/image
  - KG knowledge-graph search link
  - KG source name/link
  - KG social/profile links
  - raw KG JSON
  - target-site organic presence/rank
  - expected URL presence/rank
  - top 20 organic URLs/sources
  - timestamp

It also creates a MID/entity-cluster CSV showing which queries resolve to
which Google MID. This is intentionally a descriptive measurement rather
than a single "authority score."

# Environment

    SERPAPI_API_KEY=...

# Usage 

    python google_entity_authority.py
    python google_entity_authority.py --input input/entity_queries.csv
    python google_entity_authority.py --limit 10
    python google_entity_authority.py --no-resume

# Resources

    https://github.com/triplegem/google-kg/resources
