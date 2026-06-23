# Linux Kernel CXL Feature Tracker

## Project Purpose

`cxl_feature_tracker.py` queries the GitHub API to extract commit history from the `drivers/cxl` and `drivers/dax` directories of the [torvalds/linux](https://github.com/torvalds/linux) repository between two kernel version tags. The output is used to write blog posts documenting what changed in each kernel release for Compute Express Link (CXL) technology.

Blog posts are published at https://stevescargall.com and the blog source lives at https://github.com/sscargal/stevescargall.com.v2.

## Package Management (uv)

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. All install, run, and test commands go through uv.

```bash
uv sync                # install all dependencies (runtime + dev) — do this after cloning
uv sync --no-dev       # production install only (no pytest)
uv sync --group ai     # also install the optional anthropic SDK for AI fallback
uv lock --upgrade      # update all packages to latest allowed versions, then run uv sync
uv add <package>       # add a new runtime dependency
uv add --dev <package> # add a new development-only dependency
uv run <command>       # run any command inside the managed virtual environment
```

Dependencies are declared in `pyproject.toml`. The `uv.lock` lockfile is committed to the repo and ensures reproducible installs across machines.

To regenerate a `requirements.txt` for tools that need one:
```bash
uv export --format requirements-txt --no-dev -o requirements.txt
```

## Running the Script

### Prerequisites

```bash
uv sync          # installs requests + pytest into .venv automatically
```

A GitHub personal access token is strongly recommended. Without one, the GitHub API rate limit is 60 requests/hour (unauthenticated) vs. 5,000/hour (authenticated). The script fetches the tag date for `--start-version` plus paginated commits for each tracked path — a full release can easily exceed 60 API calls.

### Token Resolution

The token is resolved in this priority order:
1. `--ghtoken TOKEN` (CLI flag)
2. `$GITHUB_TOKEN` environment variable
3. `$GH_TOKEN` environment variable
4. No token — unauthenticated (rate-limited)

**Recommended**: set `export GITHUB_TOKEN=<your_token>` in your shell profile so you never need to pass `--ghtoken` manually.

### Basic Usage

```bash
uv run python3 cxl_feature_tracker.py [OPTIONS]
```

### All Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--ghtoken TOKEN` | string | `$GITHUB_TOKEN` | GitHub personal access token |
| `--start-version VER` | string | second-latest tag | Starting kernel version (e.g. `v6.13`) |
| `--end-version VER` | string | latest tag | Ending kernel version (e.g. `v6.14`) |
| `--output FILE` | string | stdout/auto | Write output to this file (or directory for `explainers`) |
| `--format` | `txt\|md\|json\|hugo\|podcast\|video-short\|explainers` | none | Output format |
| *(hugo date)* | — | kernel release date | The `date:` field in hugo front matter is set to the `--end-version` tag date, not today |
| `--verbose` | flag | off | Include commit URLs in terminal output |
| `--list-tags` | flag | off | Print all stable kernel tags and exit |
| `--paths PATH…` | string list | `drivers/cxl drivers/dax` | Kernel repo paths to scan |
| `--author NAME` | string | `Steve Scargall` | Author name for hugo/podcast output |
| `--ai` | flag | off | Use Claude AI for enhanced content generation |
| `--ai-model MODEL` | string | `claude-sonnet-4-6` | Claude model for SDK fallback path |

`--start-version` and `--end-version` must be provided together or not at all.

### Default Behaviour (no `--start-version` / `--end-version`)

The script fetches all stable tags (excludes `-rc` tags) and defaults to the **two most recent stable releases**. Versions are sorted semantically, so `v6.10` correctly sorts after `v6.9`.

```bash
# Uses latest two stable tags automatically
uv run python3 cxl_feature_tracker.py
```

### Output Formats

| `--format` | `--output` | AI? | Result |
|---|---|---|---|
| *(none)* | *(none)* | — | Commit titles to stdout, one per line |
| *(none)* | `FILE` | — | Commit titles to file, one per line |
| `md` | *(none)* | — | `- [title](url)` markdown links to stdout |
| `md` | `FILE` | — | `- [title](url)` markdown links written to file |
| `txt` | `FILE` | — | Commit titles only, written to file |
| `json` | `FILE` | — | JSON array of `[title, url]` pairs |
| `hugo` | *(none)* | optional | Hugo post → `{to_version}-cxl-changes.md`; stats table always, AI intro with `--ai` |
| `hugo` | `FILE` | optional | Same, written to `FILE` |
| `podcast` | *(none)* | required | Full episode script → `{to_version}-podcast-script.md` |
| `podcast` | `FILE` | required | Same, written to `FILE` |
| `video-short` | *(none)* | required | 60–90 sec YouTube Shorts script → `{to_version}-video-short-script.md` |
| `video-short` | `FILE` | required | Same, written to `FILE` |
| `explainers` | *(none)* | required | Per-feature outlines → `{to_version}-explainers/` directory |
| `explainers` | `DIR` | required | Same, written to `DIR` |

**`--verbose`** adds commit URLs to the default (no `--format`) terminal output.

### AI Content Generation

The `--ai` flag enables Claude AI to generate enhanced prose. Two paths are tried in order:

1. **`claude` CLI** (primary) — invoked as `claude -p "<prompt>"`. Uses the user's Claude subscription. No API key needed. Works if Claude Code or Claude Desktop is installed.
2. **`ANTHROPIC_API_KEY`** (fallback) — uses the `anthropic` Python SDK. Install with `uv sync --group ai`. The model is set with `--ai-model` (default: `claude-sonnet-4-6`).

If neither is available, `--format hugo` falls back to heuristic-only output. `podcast`, `video-short`, and `explainers` exit with an error if AI is unavailable.

The script sends at most 20 commits per category to the AI to keep prompts under ~8,000 tokens.

### Common Examples

```bash
# List all stable kernel tags
uv run python3 cxl_feature_tracker.py --list-tags

# Default: latest two stable releases, titles only to stdout
uv run python3 cxl_feature_tracker.py

# Specific range, markdown to stdout (preview before blog post)
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 --format md

# Generate a complete Hugo blog post in one step (heuristic summary, no AI)
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 --format hugo

# Hugo post with AI-generated intro and key changes section
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 \
  --format hugo --ai --output index.md

# Generate a full podcast episode script
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 \
  --format podcast --ai

# Generate a YouTube Shorts script
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 \
  --format video-short --ai

# Generate per-feature explainer video outlines
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 \
  --format explainers --ai

# Scan additional kernel paths beyond the defaults
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 \
  --paths drivers/cxl drivers/dax include/linux/cxl Documentation/driver-api/cxl \
  --format md

# JSON output for further processing
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 \
  --format json --output changes.json
```

Version tags can be specified with or without the leading `v` — the script normalises them automatically (e.g. `6.14` → `v6.14`).

## Tracked Kernel Paths

Default paths scanned for commits:

- `drivers/cxl` — core CXL driver subsystem
- `drivers/dax` — DAX (Direct Access) device driver

Use `--paths` to add or replace these (e.g. `include/linux/cxl`, `tools/testing/cxl`).

Commits are **deduplicated by SHA** across all paths, so a commit touching both `drivers/cxl` and `drivers/dax` appears only once in the output.

## Blog Post Workflow

Blog posts live in the repo at https://github.com/sscargal/stevescargall.com.v2 under:
```
content/english/blog/<YEAR>/<MONTH>/linux-kernel-<VERSION>-cxl-changes/index.md
```

### Full content suite (canonical workflow)

Every new kernel release should produce **all five content types** in the same output directory. Run these five commands in order for each version pair, substituting `PREV` and `VER`:

```bash
DIR="/tmp/blogs/<YEAR>/<MM>/linux-kernel-v<VER>-cxl-changes"
mkdir -p "$DIR"

# 1. Hugo blog post (also downloads featured_image.webp automatically)
uv run python3 cxl_feature_tracker.py \
  --start-version v<PREV> --end-version v<VER> \
  --format hugo --ai --output "$DIR/index.md"

# 2. Podcast episode script
uv run python3 cxl_feature_tracker.py \
  --start-version v<PREV> --end-version v<VER> \
  --format podcast --ai --output "$DIR/v<VER>-podcast-script.md"

# 3. YouTube overview script (general, ~5 min)
uv run python3 cxl_feature_tracker.py \
  --start-version v<PREV> --end-version v<VER> \
  --format video-short --ai --output "$DIR/v<VER>-video-overview-script.md"

# 4. Per-feature explainer outlines
uv run python3 cxl_feature_tracker.py \
  --start-version v<PREV> --end-version v<VER> \
  --format explainers --ai --output "$DIR/v<VER>-explainers/"

# 5. Focused YouTube Short (AI-judged — only create if a feature warrants it)
#    Read the explainer outlines, then ask Claude:
claude -p "You are evaluating CXL kernel features for a YouTube Short. Here are the
explainer outlines for kernel v<VER>: $(cat $DIR/v<VER>-explainers/*.md)
Is any single feature important and interesting enough to warrant its own dedicated
60-90 second YouTube Short aimed at engineers? Reply YES or NO, and if YES, the
feature name and a complete 60-90 second script."
#    If Claude says YES, save the script to: $DIR/v<VER>-focused-short-script.md
```

**YEAR/MM** comes from the kernel release date, which the script resolves automatically from the GitHub tag (same date used for the `date:` front matter field). Resolve it first if needed:

```bash
uv run python3 -c "
import sys; sys.path.insert(0,'.')
from cxl_feature_tracker import resolve_tag_date
print(resolve_tag_date('v<VER>', '').split('T')[0])
"
```

The resulting directory layout per version:

```
<YEAR>/<MM>/linux-kernel-v<VER>-cxl-changes/
  index.md                        # Hugo blog post
  featured_image.webp             # downloaded automatically by --format hugo
  v<VER>-podcast-script.md        # podcast episode
  v<VER>-video-overview-script.md # YouTube overview
  v<VER>-explainers/              # per-feature outlines (one .md per feature)
  v<VER>-focused-short-script.md  # only if AI judges a feature warrants it
```

**Date field:** The `date:` field in Hugo front matter is automatically set to the actual release date of `--end-version` (from the GitHub tag), not today's date. This ensures blog posts sort correctly on the site even when written retrospectively.

**Featured image:** `--format hugo` automatically downloads `featured_image.webp` from the blog repo into the same directory as `index.md`. No manual step needed.

**SEO fields:** `meta_title` and `description` are AI-generated when `--ai` is used, or populated with keyword-optimised heuristics otherwise. They are never left empty.

### Manual path: `--format md` + template

1. Run the tracker:
   ```bash
   uv run python3 cxl_feature_tracker.py \
     --start-version v<PREV> --end-version v<VERSION> \
     --format md --output changes.md
   ```
2. Create `content/english/blog/<YEAR>/<MONTH>/linux-kernel-<VERSION>-cxl-changes/index.md` with:

   ```yaml
   ---
   title: "Linux Kernel <VERSION> is Released: This is What's New for Compute Express Link (CXL)"
   meta_title: ""
   description: ""
   date: <YYYY-MM-DDT00:00:00Z>
   image: "featured_image.webp"
   categories: ["CXL"]
   author: "Steve Scargall"
   tags: ["CXL", "Linux", "Kernel"]
   draft: false
   aliases:
   ---

   The Linux Kernel <VERSION> release brings several improvements and additions related to Compute Express Link (CXL) technology.

   ## CXL related changes from Kernel v<PREV> to v<VERSION>

   Here is the detailed list of all commits merged into the <VERSION> Kernel for CXL and DAX. This list was generated by the [Linux Kernel CXL Feature Tracker](https://github.com/sscargal/linux-cxl-tracker).

   <PASTE CONTENTS OF changes.md HERE>
   ```

3. Set `draft: false` when ready to publish.

## Running Tests

```bash
uv run pytest tests/ -v
```

Tests are in `tests/test_cxl_feature_tracker.py` and are fully offline — no GitHub token or network access required. All HTTP calls are mocked.

## GitHub API Notes

- **Rate limits**: 60 req/hr unauthenticated, 5,000 req/hr with a token. Always set `$GITHUB_TOKEN`.
- **Pagination**: The script follows `Link: rel="next"` headers automatically.
- **Commit range**: The script uses a SHA set-difference approach. It builds the full set of commit SHAs reachable from `--start-version` (`from_shas`), then fetches all commits reachable from `--end-version` and keeps only those whose SHA is absent from `from_shas`. This correctly handles CXL patches that are committed to subsystem trees weeks before a release and therefore carry committer dates earlier than the previous kernel tag — a date-based `since=` filter would silently drop those commits.
- **Timeout**: All API requests use a 30-second timeout. Rate limit errors (403/429) print a clear message with the reset time and exit.
