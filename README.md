# 🎵 Music Promo Pipeline

A daily-automated pipeline that surfaces **playlist curators**, **online communities**, and **YouTube / streaming creators** currently accepting music submissions — tailored to your specific release.

Powered by [Claude](https://anthropic.com/claude) with live web search. Results are committed back to this repo every day automatically.

---

## How It Works

```
config.yaml  ──▶  pipeline.py  ──▶  Claude API (+ web search)  ──▶  results/
                                        │
                         ┌─────────────┼─────────────────┐
                         ▼             ▼                  ▼
                 Playlist Curators  Communities    YouTube / Streamers
```

Each category is searched independently. Every lead gets a **quality score (1–5)** and only those meeting your `min_quality_score` threshold make the final output. Results are saved as **JSON**, optionally **CSV**, and a human-readable **Markdown report**.

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/music-promo-pipeline.git
cd music-promo-pipeline
```

### 2. Edit `config.yaml`

Fill in your artist name, release details, genres, moods, and similar artists. The more specific you are, the better the leads.

### 3. Set your API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Get a key at [console.anthropic.com](https://console.anthropic.com).

### 4. Install dependencies & run

```bash
pip install -r requirements.txt
python src/pipeline.py
```

Results appear in `results/leads_YYYY-MM-DD.json` and `results/leads_YYYY-MM-DD.md`.

---

## GitHub Actions (Daily Auto-Run)

### Set up the secret

1. Go to your repo → **Settings → Secrets and variables → Actions**
2. Click **New repository secret**
3. Name: `ANTHROPIC_API_KEY`  
   Value: your Anthropic API key

The workflow (`.github/workflows/daily.yml`) will then run every day at **08:00 UTC** and:
- Run the pipeline against your `config.yaml`
- Commit new results back into `results/`
- Upload an artifact you can download from the Actions tab

### Manual trigger

Go to **Actions → 🎵 Daily Music Promo Pipeline → Run workflow** to fire it on demand. You can also enable "Dry run" to test the workflow without spending API credits.

### Change the schedule

Edit the cron in `.github/workflows/daily.yml`:
```yaml
- cron: "0 8 * * *"   # daily at 08:00 UTC
- cron: "0 8 * * 1"   # weekly on Mondays
```

---

## Config Reference

| Field | Description |
|---|---|
| `project.artist_name` | Your artist name |
| `project.release_title` | EP / album / single name |
| `project.description` | 1–2 sentence pitch used in prompts |
| `music.genres` | List of genres (be specific) |
| `music.similar_artists` | Used to target similar-audience curators |
| `pipeline.max_results_per_category` | Max raw leads per category (default 20) |
| `pipeline.min_quality_score` | Drop leads below this score 1–5 (default 3) |
| `pipeline.categories` | Which categories to search |
| `output.format` | `json` \| `csv` \| `both` |
| `output.save_markdown` | Also write a `.md` report |

---

## Output Files

```
results/
  leads_2025-06-01.json    ← all categories, structured data
  leads_2025-06-01.md      ← human-readable report with scores
  playlists_curators_2025-06-01.csv   ← (if format: csv or both)
  online_communities_2025-06-01.csv
  youtube_streamers_2025-06-01.csv
```

### Lead fields (all categories share a common core)

| Field | Description |
|---|---|
| `name` | Curator / community / creator name |
| `platform` | Where they live (Spotify, Reddit, YouTube…) |
| `url` | Profile / community link |
| `submission_url` | Where to actually submit |
| `submission_method` | Email / Form / SubmitHub / Groover / DM |
| `quality_score` | 1–5 (5 = best fit, high confidence) |
| `notes` | Why it's a good fit; submission tips |
| `last_verified` | Date Claude confirmed it was active |

---

## Tips

- **Be specific in `config.yaml`**: vague genres get generic results. "melodic death metal with black metal atmosphere" beats "metal".
- **Run after major releases**: curators often open submissions around playlist refresh cycles.
- **Check `notes` field**: Claude will flag if a submission process looks uncertain.
- **Combine with SubmitHub / Groover**: use this pipeline to find curators *not* on those platforms.

---

## Cost

Each daily run makes ~3 Claude API calls with web search. Typical cost: **$0.05–$0.20 per run** depending on response length.

---

## License

MIT — use freely, fork, improve.
