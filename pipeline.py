"""
music_promo_pipeline.py
───────────────────────
Daily pipeline that, given a music project config, uses the Claude API
(with web search) to surface:
  1. Playlist curators accepting submissions
  2. Online communities (Reddit, Discord, forums)
  3. YouTube / Twitch / streaming creators accepting music

Run:
    python src/pipeline.py                   # uses config.yaml
    python src/pipeline.py --config my.yaml  # custom config path
    python src/pipeline.py --dry-run         # skip API calls, show prompts
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import yaml

# ──────────────────────────────────────────────────────────────────────────────
# Prompts
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a music promotion research assistant with deep knowledge of
the independent music ecosystem. Your job is to find REAL, VERIFIED, CURRENTLY-ACTIVE
opportunities for indie artists to submit their music.

Rules you MUST follow:
- Only surface sources that are genuinely accepting submissions RIGHT NOW (or have
  a clear, active submission process). Verify this via web search before including.
- Prioritise recency: favour sources active in the last 6 months.
- Quality over quantity: a smaller list of high-confidence leads beats a long list of
  uncertain ones.
- For each lead, provide a quality score (1–5) and a short honest note on why it made
  the cut (and any caveats).
- Return ONLY valid JSON — no markdown fences, no commentary outside JSON.
"""

PLAYLIST_PROMPT = """
Search the web to find Spotify playlist curators, Apple Music curators, and music
blogs that are CURRENTLY accepting unsolicited submissions from independent artists
whose music fits this profile:

ARTIST PROFILE:
{profile}

For each lead, search for evidence of:
  - An active submission form, email, or SubmitHub / Groover page
  - Recent playlist updates or blog posts (within 6 months ideally)
  - Follower / subscriber counts where findable

Return a JSON array of up to {max_results} objects with these fields:
[
  {{
    "name": "Curator or playlist name",
    "platform": "Spotify | Apple Music | Blog | SubmitHub | Groover | Other",
    "url": "direct link to submission page or playlist",
    "submission_url": "link to submission form/email if different from url",
    "follower_count": "number or null if unknown",
    "genres_covered": ["genre1", "genre2"],
    "submission_method": "SubmitHub | Groover | Email | Website Form | Free",
    "cost": "free | paid | both",
    "quality_score": 4,
    "notes": "Why this is a good fit; any caveats",
    "last_verified": "YYYY-MM-DD"
  }}
]
"""

COMMUNITY_PROMPT = """
Search the web to find ACTIVE online communities where the target artist could
genuinely share their music, get feedback, or network — NOT just spam-post.

Look for:
  - Subreddits that allow music submissions (check sidebar rules via search)
  - Discord servers (music genre-specific, feedback, playlist pitching, A&R)
  - Facebook groups, forums, Lemmy communities, etc.
  - Communities specifically for the genres below

ARTIST PROFILE:
{profile}

Only include communities where:
  - Sharing music by members is explicitly allowed
  - The community appears active (posts within last 30 days)

Return a JSON array of up to {max_results} objects:
[
  {{
    "name": "Community name",
    "platform": "Reddit | Discord | Facebook | Forum | Other",
    "url": "link to community",
    "member_count": "number or null",
    "submission_rules": "brief summary of what is/isn't allowed",
    "genres_focus": ["genre1"],
    "activity_level": "high | medium | low",
    "quality_score": 4,
    "notes": "Why useful; submission tips",
    "last_verified": "YYYY-MM-DD"
  }}
]
"""

CREATOR_PROMPT = """
Search the web to find YouTube channels, Twitch streamers, TikTok creators, and
podcast hosts who:
  1. Feature or react to independent music in the relevant genres
  2. Have an active submission process (email, form, or stated openness to demos)
  3. Have posted content within the last 3 months

ARTIST PROFILE:
{profile}

Prioritise channels with:
  - A dedicated "music submission" section in their description / about page
  - A history of featuring unsigned / indie artists
  - Engagement that suggests a real audience

Return a JSON array of up to {max_results} objects:
[
  {{
    "name": "Creator / channel name",
    "platform": "YouTube | Twitch | TikTok | Podcast | Other",
    "url": "channel or profile link",
    "subscriber_count": "number or null",
    "content_type": "reaction | playlist video | lo-fi stream | review | other",
    "submission_url": "link to submission info or email",
    "submission_method": "Email | Form | DM | SubmitHub | Other",
    "genres_covered": ["genre1"],
    "quality_score": 4,
    "notes": "Why a good fit; tips for pitching",
    "last_verified": "YYYY-MM-DD"
  }}
]
"""

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build_profile(cfg: dict) -> str:
    p = cfg["project"]
    m = cfg["music"]
    lines = [
        f"Artist: {p['artist_name']}",
        f"Release: {p['release_title']} ({p['release_type']}, {p['release_date']})",
        f"Description: {p['description'].strip()}",
        f"Genres: {', '.join(m['genres'])}",
        f"Moods: {', '.join(m['moods'])}",
        f"Similar Artists: {', '.join(m['similar_artists'])}",
        f"Key Instruments: {', '.join(m['key_instruments'])}",
    ]
    extras = cfg["pipeline"].get("extra_keywords", [])
    if extras:
        lines.append(f"Extra Keywords: {', '.join(extras)}")
    platforms = {k: v for k, v in m["platforms"].items() if v}
    if platforms:
        lines.append("Platforms: " + ", ".join(f"{k}: {v}" for k, v in platforms.items()))
    return "\n".join(lines)


def parse_json_response(text: str) -> list:
    """Extract JSON array from model response, tolerating minor formatting issues."""
    # Strip markdown fences if model disobeyed
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3]
    # Find first [ and last ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON parse error: {e}")
        return []


def filter_results(results: list, min_score: int) -> list:
    filtered = []
    for r in results:
        try:
            score = int(r.get("quality_score", 0))
        except (ValueError, TypeError):
            score = 0
        if score >= min_score:
            filtered.append(r)
    return sorted(filtered, key=lambda x: int(x.get("quality_score", 0)), reverse=True)


def run_search(client: anthropic.Anthropic, prompt: str, dry_run: bool) -> list:
    if dry_run:
        print("  [DRY RUN] Would send prompt to Claude API with web_search tool")
        return []

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # Collect all text blocks from potentially multi-block response
    full_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            full_text += block.text

    return parse_json_response(full_text)


# ──────────────────────────────────────────────────────────────────────────────
# Report writers
# ──────────────────────────────────────────────────────────────────────────────

def write_json(output: dict, path: Path):
    with open(path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  💾  JSON saved → {path}")


def write_csv(data: list, path: Path, fields: list):
    import csv
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    print(f"  💾  CSV saved → {path}")


def write_markdown(output: dict, path: Path):
    cfg = output["config"]
    now = output["run_date"]

    lines = [
        f"# 🎵 Music Promo Pipeline Report",
        f"**Run date:** {now}  ",
        f"**Artist:** {cfg['project']['artist_name']}  ",
        f"**Release:** {cfg['project']['release_title']} ({cfg['project']['release_type']})  ",
        "",
        "---",
        "",
    ]

    def section(title, emoji, items, fields):
        lines.append(f"## {emoji} {title}")
        lines.append(f"*{len(items)} leads found*")
        lines.append("")
        for item in items:
            score = item.get("quality_score", "?")
            stars = "⭐" * int(score) if str(score).isdigit() else ""
            lines.append(f"### {item.get('name', 'Unknown')} {stars}")
            for f in fields:
                val = item.get(f)
                if val and val not in ("null", None, ""):
                    lines.append(f"- **{f.replace('_', ' ').title()}:** {val}")
            lines.append("")

    section(
        "Playlist Curators",
        "🎧",
        output.get("playlists_curators", []),
        ["platform", "url", "submission_url", "submission_method", "cost",
         "follower_count", "genres_covered", "notes", "last_verified"],
    )
    section(
        "Online Communities",
        "🌐",
        output.get("online_communities", []),
        ["platform", "url", "member_count", "submission_rules",
         "activity_level", "notes", "last_verified"],
    )
    section(
        "Creators (YouTube / Streaming)",
        "🎬",
        output.get("youtube_streamers", []),
        ["platform", "url", "subscriber_count", "content_type",
         "submission_url", "submission_method", "notes", "last_verified"],
    )

    lines.append("---")
    lines.append(f"*Generated by music-promo-pipeline on {now}*")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  💾  Markdown saved → {path}")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Music Promo Pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML")
    parser.add_argument("--dry-run", action="store_true", help="Skip API calls")
    args = parser.parse_args()

    print("\n🎵  Music Promo Pipeline starting…\n")

    cfg = load_config(args.config)
    pipeline_cfg = cfg["pipeline"]
    profile = build_profile(cfg)
    max_r = pipeline_cfg.get("max_results_per_category", 20)
    min_score = pipeline_cfg.get("min_quality_score", 3)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("❌  ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key) if api_key else None

    categories = pipeline_cfg.get("categories", [
        "playlists_curators", "online_communities", "youtube_streamers"
    ])

    results = {}

    category_map = {
        "playlists_curators": (PLAYLIST_PROMPT, "🎧 Playlist Curators"),
        "online_communities": (COMMUNITY_PROMPT, "🌐 Online Communities"),
        "youtube_streamers":  (CREATOR_PROMPT,  "🎬 Creators / Streamers"),
    }

    for cat in categories:
        if cat not in category_map:
            print(f"⚠️  Unknown category '{cat}', skipping.")
            continue
        prompt_tpl, label = category_map[cat]
        print(f"🔍  Searching: {label}…")
        prompt = prompt_tpl.format(profile=profile, max_results=max_r)
        raw = run_search(client, prompt, args.dry_run)
        filtered = filter_results(raw, min_score)
        results[cat] = filtered
        print(f"  ✅  {len(filtered)} quality leads (from {len(raw)} raw)")
        time.sleep(2)  # be gentle with the API

    # ── Write outputs ──────────────────────────────────────────────────────────
    run_date = datetime.utcnow().strftime("%Y-%m-%d")
    out_dir = Path(cfg["output"].get("results_dir", "results"))
    out_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "run_date": run_date,
        "config": {
            "project": cfg["project"],
            "music": {k: v for k, v in cfg["music"].items() if k != "platforms"},
        },
        **results,
    }

    fmt = cfg["output"].get("format", "json")
    base = out_dir / f"leads_{run_date}"

    if fmt in ("json", "both"):
        write_json(output, base.with_suffix(".json"))

    if fmt in ("csv", "both"):
        for cat, items in results.items():
            if items:
                write_csv(
                    items,
                    out_dir / f"{cat}_{run_date}.csv",
                    list(items[0].keys()),
                )

    if cfg["output"].get("save_markdown", True):
        write_markdown(output, base.with_suffix(".md"))

    # ── Summary ───────────────────────────────────────────────────────────────
    total = sum(len(v) for v in results.values())
    print(f"\n✨  Done! {total} total leads written to {out_dir}/")
    print(f"    Run date: {run_date}\n")

    # Exit with error code if zero results (helps CI catch failures)
    if not args.dry_run and total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
