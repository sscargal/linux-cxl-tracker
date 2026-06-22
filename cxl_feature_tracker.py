#!/usr/bin/env python3
import requests
import argparse
import json
import sys
import os
import re
import time
import datetime
import shutil
import subprocess

REPO = "torvalds/linux"
BASE_URL = f"https://api.github.com/repos/{REPO}"
DEFAULT_PATHS = ["drivers/cxl", "drivers/dax"]

# ---------------------------------------------------------------------------
# Commit categorization
# ---------------------------------------------------------------------------

# Priority order: first matching category wins.
CATEGORY_KEYWORDS = {
    "New Features & Hardware": [
        "add support", "introduce", "implement", "new device", "add did",
        "add pci", "add driver", "enable support", "new feature",
    ],
    "Bug Fixes": [
        "fix", "revert", "resolve", "correct", "workaround", "avoid",
        "repair", "recover", "prevent",
    ],
    "Performance": [
        "perf", "optimiz", "faster", "reduce latency", "improve throughput",
        "speedup", "speed up", "reduce overhead",
    ],
    "Refactoring & Cleanup": [
        "refactor", "cleanup", "clean up", "rename", "reorganize", "simplify",
        "remove", "rework", "consolidate", "deduplicate",
    ],
    "Testing": [
        "test", "selftests", "selftest",
    ],
    "Documentation": [
        "doc", "comment", "documentation", "readme", "spelling", "typo",
    ],
}

CATEGORY_ORDER = [
    "New Features & Hardware",
    "Bug Fixes",
    "Performance",
    "Refactoring & Cleanup",
    "Testing",
    "Documentation",
    "Other",
]


def categorize_commits(commits):
    """Assign each commit to a category using keyword matching (first match wins)."""
    categories = {cat: [] for cat in CATEGORY_ORDER}
    for msg, url in commits:
        msg_lower = msg.lower()
        assigned = False
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                categories[cat].append((msg, url))
                assigned = True
                break
        if not assigned:
            categories["Other"].append((msg, url))
    return categories


def build_ai_context(categories, from_version, to_version, max_per_cat=20):
    """Build a compact commit summary suitable for inclusion in an AI prompt."""
    total = sum(len(v) for v in categories.values())
    lines = [
        f"Linux kernel CXL/DAX commit summary: {from_version} → {to_version}",
        f"Total commits: {total}",
        "",
        "Counts by category:",
    ]
    for cat in CATEGORY_ORDER:
        count = len(categories.get(cat, []))
        if count:
            lines.append(f"  {cat}: {count}")

    lines.append("")
    lines.append(f"Representative commits (up to {max_per_cat} per category):")
    for cat in CATEGORY_ORDER:
        commits = categories.get(cat, [])
        if not commits:
            continue
        lines.append(f"\n### {cat} ({len(commits)} total):")
        for msg, _ in commits[:max_per_cat]:
            lines.append(f"  - {msg}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AI integration
# ---------------------------------------------------------------------------

def call_ai(prompt, model="claude-sonnet-4-6", max_tokens=4096):
    """Call Claude via the claude CLI (subscription) or ANTHROPIC_API_KEY SDK.

    Returns the response text, or None if no AI is available.
    Primary path: claude CLI (uses the user's Claude subscription, no API key needed).
    Fallback: ANTHROPIC_API_KEY environment variable + anthropic SDK.
    """
    # Primary: claude CLI
    if shutil.which("claude"):
        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            if result.stderr:
                print(f"Warning: claude CLI error: {result.stderr[:200]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("Warning: claude CLI timed out.", file=sys.stderr)
        except Exception as e:
            print(f"Warning: claude CLI failed: {e}", file=sys.stderr)

    # Fallback: ANTHROPIC_API_KEY + SDK
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic  # optional dependency: uv sync --group ai
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except ImportError:
            print(
                "Warning: anthropic package not installed. "
                "Run: uv sync --group ai",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"Warning: Anthropic API call failed: {e}", file=sys.stderr)

    return None


def _generate_hugo_ai_content(categories, from_version, to_version, model):
    """Ask AI to write an intro + key-changes section for the hugo post."""
    context = build_ai_context(categories, from_version, to_version)
    prompt = f"""You are a Linux kernel CXL/DAX expert writing for a technical blog aimed at \
hardware engineers, kernel developers, and the CXL ecosystem.

{context}

Write the following two sections for a blog post about Linux {to_version} CXL/DAX changes. \
Return only Markdown — no preamble or commentary.

1. An introduction (2–3 paragraphs, no heading) that explains the most significant themes \
of this release in plain English. Be specific: name actual subsystems, features, or fixes \
that stand out. Avoid generic filler phrases.

2. A "### Key Changes" section with 5–8 bullet points. Each bullet: \
**Bold feature name**: one or two sentences describing what changed and why it matters.

Do not include a stats table — that is generated separately."""

    return call_ai(prompt, model=model, max_tokens=2048)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _make_headers(token):
    return {'Authorization': f'token {token}'} if token else {}


def _api_get(url, token):
    """GET with 30s timeout and rate-limit detection. Returns None on network error."""
    headers = _make_headers(token)
    try:
        response = requests.get(url, headers=headers, timeout=30)
    except requests.exceptions.Timeout:
        print(f"Error: request timed out fetching {url}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Error: network error: {e}", file=sys.stderr)
        return None

    if response.status_code in (403, 429):
        reset = int(response.headers.get('X-RateLimit-Reset', 0))
        wait = max(int(reset - time.time()), 1)
        print(
            f"Error: GitHub API rate limit exceeded. Resets in {wait}s. "
            f"Use --ghtoken or set $GITHUB_TOKEN / $GH_TOKEN.",
            file=sys.stderr
        )
        sys.exit(1)

    return response


def get_tags(token):
    """Fetch all stable (non-RC) kernel tags, sorted by version number."""
    url = f"{BASE_URL}/tags"
    tags = []

    while url:
        response = _api_get(url, token)
        if response is None:
            return []
        if response.status_code == 200:
            try:
                page_data = response.json()
                tags.extend(tag['name'] for tag in page_data)
            except (KeyError, TypeError, ValueError) as e:
                print(f"Error: unexpected response parsing tags: {e}", file=sys.stderr)
                sys.exit(1)
            url = response.links.get('next', {}).get('url')
        else:
            print(
                f"Error: failed to fetch tags: {response.status_code} {response.reason}",
                file=sys.stderr
            )
            return []

    stable_tags = sorted(
        [tag for tag in tags if "-rc" not in tag],
        key=lambda x: tuple(int(p) for p in re.findall(r'\d+', x))
    )
    return stable_tags


def resolve_tag_date(tag, token):
    """Return the committer date (ISO 8601) of a tag's tip commit."""
    url = f"{BASE_URL}/commits/{tag}"
    response = _api_get(url, token)
    if response is None:
        print(f"Error: could not resolve tag '{tag}' to a commit date.", file=sys.stderr)
        sys.exit(1)
    if response.status_code != 200:
        print(
            f"Error: could not resolve tag '{tag}': {response.status_code} {response.reason}",
            file=sys.stderr
        )
        sys.exit(1)
    try:
        data = response.json()
        return data['commit']['committer']['date']
    except (KeyError, TypeError, ValueError) as e:
        print(f"Error: unexpected response resolving tag '{tag}': {e}", file=sys.stderr)
        sys.exit(1)


def get_commits(from_tag, to_tag, token, paths=None):
    """Fetch commits in (from_tag, to_tag] for the given paths, deduplicated by SHA."""
    if paths is None:
        paths = DEFAULT_PATHS

    from_date = resolve_tag_date(from_tag, token)
    seen = set()
    commits = []

    for path in paths:
        url = f"{BASE_URL}/commits?sha={to_tag}&path={path}&since={from_date}"
        page_num = 0

        while url:
            page_num += 1
            print(f"  [{path}] fetching page {page_num}...", end='\r', file=sys.stderr)

            response = _api_get(url, token)
            if response is None:
                break

            if response.status_code == 200:
                try:
                    page_commits = response.json()
                except (ValueError, TypeError) as e:
                    print(f"\nError: unexpected response for '{path}': {e}", file=sys.stderr)
                    break

                url = response.links.get('next', {}).get('url')

                for commit in page_commits:
                    try:
                        sha = commit.get('sha')
                        if sha and sha not in seen:
                            seen.add(sha)
                            message = commit['commit']['message'].split('\n')[0]
                            commit_url = commit['html_url']
                            commits.append((message, commit_url))
                    except (KeyError, TypeError):
                        continue
            else:
                print(
                    f"\nError: failed to fetch commits for '{path}': "
                    f"{response.status_code} {response.reason}",
                    file=sys.stderr
                )
                break

        print(f"  [{path}] done ({page_num} page(s)).       ", file=sys.stderr)

    return commits


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def validate_version(version, tags):
    """Normalise and validate a version string against known tags."""
    if not version.startswith('v'):
        version = 'v' + version
    if version not in tags:
        raise ValueError(f"Invalid version '{version}'.")
    return version


def write_output(commits, output, fmt):
    """Write commits to a file in the requested format."""
    try:
        with open(output, 'w') as f:
            if fmt == 'md':
                for message, url in commits:
                    f.write(f"- [{message}]({url})\n")
            elif fmt == 'json':
                json.dump(commits, f, indent=4)
            else:
                for message, _ in commits:
                    f.write(message + '\n')
    except IOError as e:
        print(f"Error: could not write to '{output}': {e}", file=sys.stderr)


def _build_stats_table(categories):
    """Return a Markdown stats table string for non-empty categories."""
    lines = ["| Category | Commits |", "|---|---|"]
    for cat in CATEGORY_ORDER:
        count = len(categories.get(cat, []))
        if count:
            lines.append(f"| {cat} | {count} |")
    return "\n".join(lines)


def write_hugo_output(commits, output, from_version, to_version, author,
                      categories=None, ai_content=None, release_date=None):
    """Write a complete Hugo blog post with YAML front matter and optional AI content.

    release_date: YYYY-MM-DD string of the to_version kernel release date.
    If omitted, falls back to today's date (useful for testing).
    """
    date_str = release_date if release_date else datetime.date.today().isoformat()
    version_num = to_version.lstrip('v')
    total = len(commits)

    front_matter = (
        f"---\n"
        f"title: \"Linux Kernel {to_version} is Released: This is What's New for Compute Express Link (CXL)\"\n"
        f"meta_title: \"\"\n"
        f"description: \"\"\n"
        f"date: {date_str}T00:00:00Z\n"
        f"image: \"featured_image.webp\"\n"
        f"categories: [\"CXL\"]\n"
        f"author: \"{author}\"\n"
        f"tags: [\"CXL\", \"Linux\", \"Kernel\"]\n"
        f"draft: false\n"
        f"aliases:\n"
        f"---\n\n"
    )

    # Fixed intro
    intro = (
        f"The Linux Kernel {to_version} release brings several improvements and additions "
        f"related to Compute Express Link (CXL) technology.\n\n"
    )

    # Highlights section
    highlights = f"## Release Highlights\n\n"
    highlights += f"Linux Kernel {to_version} includes **{total} commits** to the CXL and DAX subsystems"

    if categories:
        highlights += ":\n\n"
        highlights += _build_stats_table(categories) + "\n"
    else:
        highlights += ".\n"

    # AI-generated intro and key changes
    ai_section = ""
    if ai_content:
        ai_section = f"\n{ai_content}\n"

    # Full commit list
    commit_section = (
        f"\n## CXL related changes from Kernel {from_version} to {to_version}\n\n"
        f"Here is the detailed list of all commits merged into the {version_num} Kernel for CXL and DAX. "
        f"This list was generated by the "
        f"[Linux Kernel CXL Feature Tracker](https://github.com/sscargal/linux-cxl-tracker).\n\n"
    )
    commit_list = ''.join(f"- [{msg}]({url})\n" for msg, url in commits)

    body = intro + highlights + ai_section + commit_section + commit_list

    try:
        with open(output, 'w') as f:
            f.write(front_matter + body)
    except IOError as e:
        print(f"Error: could not write to '{output}': {e}", file=sys.stderr)


def write_podcast_output(commits, categories, output, from_version, to_version, author, model):
    """Write a full conversational podcast episode script using AI."""
    context = build_ai_context(categories, from_version, to_version)
    total = len(commits)
    prompt = f"""You are writing a podcast script for a technical audio show about Linux kernel development.

Audience: Linux kernel developers, hardware engineers, and the CXL ecosystem.
Episode title: "What's New in Linux {to_version} for CXL and DAX"
Host name: {author}
Tone: Conversational, technically accurate, and engaging. Define jargon when first used.

{context}

Write a complete podcast script with the following sections. \
Use [PAUSE], [EMPHASIS: text], and [TRANSITION] cues where natural.

[INTRO] — ~30 seconds of spoken text. Welcome the listener, state the episode topic.
[OVERVIEW] — ~60 seconds. Total commits, the 3–4 dominant themes of this release.
[SEGMENT: New Features & Hardware] — cover notable new hardware support and features (if any)
[SEGMENT: Bug Fixes] — cover significant fixes (if any)
[SEGMENT: Performance] — cover performance improvements (if any)
[DEEP DIVE] — pick the 2–3 most technically interesting commits for a closer look. \
Explain what the code does and why it matters, as if speaking to an engineer.
[OUTRO] — ~20 seconds. Summary, where to find the full commit list, sign-off.

Omit any [SEGMENT] that has zero commits. \
Return only the script — no meta-commentary."""

    print("Generating podcast script (this may take 30–60 seconds)...", file=sys.stderr)
    ai_text = call_ai(prompt, model=model, max_tokens=6000)
    if not ai_text:
        print("Error: AI content generation failed. Check claude CLI or $ANTHROPIC_API_KEY.", file=sys.stderr)
        sys.exit(1)

    header = (
        f"# Podcast Script: What's New in Linux {to_version} for CXL and DAX\n\n"
        f"**Host:** {author}  \n"
        f"**Kernel range:** {from_version} → {to_version}  \n"
        f"**Total commits:** {total}  \n"
        f"**Generated:** {datetime.date.today().isoformat()}\n\n"
        f"---\n\n"
    )

    try:
        with open(output, 'w') as f:
            f.write(header + ai_text + "\n")
    except IOError as e:
        print(f"Error: could not write to '{output}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Podcast script written to: {output}", file=sys.stderr)


def write_video_short_output(commits, categories, output, from_version, to_version, model):
    """Write a 60–90 second YouTube Shorts script using AI."""
    context = build_ai_context(categories, from_version, to_version, max_per_cat=10)
    prompt = f"""Write a 60–90 second YouTube Shorts script about the Linux {to_version} CXL kernel changes.

{context}

Rules:
- Open with a hook in the first 3 seconds that grabs attention.
- Cover 5–7 of the most impactful changes, one sentence each.
- Use plain language — assume the viewer is a Linux developer but not a CXL expert.
- End with: "Full details and the complete commit list are in the blog post — link in description."
- Format each line as: [VISUAL CUE] Spoken text.
- Visual cues should be brief and actionable, e.g. [Show commit title], [Text overlay: "N bug fixes"], [B-roll: kernel code].
- Total spoken word count: 150–200 words.

Return only the script."""

    print("Generating video-short script (this may take 20–40 seconds)...", file=sys.stderr)
    ai_text = call_ai(prompt, model=model, max_tokens=1024)
    if not ai_text:
        print("Error: AI content generation failed. Check claude CLI or $ANTHROPIC_API_KEY.", file=sys.stderr)
        sys.exit(1)

    header = (
        f"# YouTube Short Script: Linux {to_version} CXL Changes\n\n"
        f"**Kernel range:** {from_version} → {to_version}  \n"
        f"**Target length:** 60–90 seconds  \n"
        f"**Generated:** {datetime.date.today().isoformat()}\n\n"
        f"---\n\n"
    )

    try:
        with open(output, 'w') as f:
            f.write(header + ai_text + "\n")
    except IOError as e:
        print(f"Error: could not write to '{output}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Video-short script written to: {output}", file=sys.stderr)


def write_explainers_output(commits, categories, output_dir, from_version, to_version, model):
    """Generate one explainer video outline per key feature using AI."""
    context = build_ai_context(categories, from_version, to_version)

    # Step 1: Ask AI to identify the most explainer-worthy features
    identify_prompt = f"""You are a Linux kernel CXL/DAX expert.

{context}

Identify the 5 most "explainer-worthy" features or changes in this release — \
things that introduce a new concept, add hardware support, or make a significant \
architectural change. Exclude pure bug fixes, cleanups, and documentation commits.

Return a JSON array only (no other text). Each item:
{{
  "name": "short feature name (5 words max)",
  "description": "one sentence explaining what it is",
  "why_interesting": "one sentence on why a developer would want to understand this",
  "relevant_commits": ["commit message 1", "commit message 2"]
}}"""

    print("Identifying key features for explainer videos...", file=sys.stderr)
    features_json = call_ai(identify_prompt, model=model, max_tokens=2048)
    if not features_json:
        print("Error: AI content generation failed. Check claude CLI or $ANTHROPIC_API_KEY.", file=sys.stderr)
        sys.exit(1)

    # Parse JSON — strip any markdown fences if present
    features_text = features_json.strip()
    if features_text.startswith("```"):
        features_text = re.sub(r"^```[a-z]*\n?", "", features_text)
        features_text = re.sub(r"\n?```$", "", features_text)

    try:
        features = json.loads(features_text)
    except json.JSONDecodeError as e:
        print(f"Error: could not parse feature list from AI: {e}", file=sys.stderr)
        print(f"Raw response:\n{features_json[:500]}", file=sys.stderr)
        sys.exit(1)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Step 2: Generate an outline for each feature
    for i, feature in enumerate(features, 1):
        name = feature.get("name", f"feature-{i}")
        description = feature.get("description", "")
        why = feature.get("why_interesting", "")
        rel_commits = feature.get("relevant_commits", [])

        outline_prompt = f"""You are a Linux kernel educator creating YouTube explainer video content.

Feature: {name}
Description: {description}
Why interesting: {why}
Relevant commits from Linux {to_version}:
{chr(10).join(f'  - {c}' for c in rel_commits)}

Write a structured outline for a 5–10 minute explainer YouTube video about this feature. \
Format as Markdown with these sections:
## Suggested Title (punchy, SEO-friendly, under 70 characters)
## Hook (first 15 seconds — grab the viewer)
## Background (what problem existed before / what is CXL in this context)
## Technical Explanation (the key concept, how the code/feature works — use analogies)
## Demo Ideas (concrete things to show on screen or in a terminal)
## Real-World Impact (who benefits and how)
## Summary & Call to Action
## Key Commits to Reference (list the relevant commit messages with a brief note on each)
## Estimated Video Length

Return only the Markdown outline."""

        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        filename = f"{i:02d}-{slug}.md"
        filepath = os.path.join(output_dir, filename)

        print(f"Generating explainer outline {i}/{len(features)}: {name}...", file=sys.stderr)
        outline = call_ai(outline_prompt, model=model, max_tokens=2048)
        if not outline:
            print(f"Warning: AI failed for feature '{name}', skipping.", file=sys.stderr)
            continue

        header = (
            f"# Explainer Outline: {name}\n\n"
            f"**Kernel release:** {to_version} ({from_version} → {to_version})  \n"
            f"**Generated:** {datetime.date.today().isoformat()}\n\n"
            f"---\n\n"
        )

        try:
            with open(filepath, 'w') as f:
                f.write(header + outline + "\n")
        except IOError as e:
            print(f"Error writing {filepath}: {e}", file=sys.stderr)

    print(f"Explainer outlines written to: {output_dir}/", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description="Track CXL feature changes in the Linux kernel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  GITHUB_TOKEN     GitHub personal access token (used if --ghtoken is not set)\n"
            "  GH_TOKEN         Alternative token variable name\n"
            "  ANTHROPIC_API_KEY  Fallback for AI content generation (used if claude CLI absent)\n"
            "\n"
            "AI content generation (--ai flag):\n"
            "  Primary:  claude CLI — uses your Claude subscription, no API key needed\n"
            "  Fallback: ANTHROPIC_API_KEY env var + anthropic SDK (uv sync --group ai)\n"
            "\n"
            "Examples:\n"
            "  %(prog)s --list-tags\n"
            "  %(prog)s --start-version v6.13 --end-version v6.14 --format md\n"
            "  %(prog)s --start-version v6.13 --end-version v6.14 --format hugo --ai\n"
            "  %(prog)s --start-version v6.13 --end-version v6.14 --format podcast --ai\n"
            "  %(prog)s --start-version v6.13 --end-version v6.14 --format explainers --ai\n"
        )
    )
    parser.add_argument(
        '--ghtoken', type=str,
        help='GitHub API token (or set $GITHUB_TOKEN / $GH_TOKEN)'
    )
    parser.add_argument('--start-version', type=str, help='Starting kernel version (e.g. v6.13)')
    parser.add_argument('--end-version', type=str, help='Ending kernel version (e.g. v6.14)')
    parser.add_argument('--output', type=str, help='Output file (or directory for --format explainers)')
    parser.add_argument(
        '--format',
        choices=['txt', 'md', 'json', 'hugo', 'podcast', 'video-short', 'explainers'],
        default=None,
        help=(
            'Output format. txt/md/json: commit list. '
            'hugo: full blog post with summary. '
            'podcast/video-short/explainers: AI-generated scripts (requires --ai).'
        )
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Include commit URLs in default terminal output'
    )
    parser.add_argument(
        '--list-tags', action='store_true',
        help='List all stable kernel tags and exit'
    )
    parser.add_argument(
        '--paths', nargs='+', default=list(DEFAULT_PATHS), metavar='PATH',
        help='Kernel repo paths to scan (default: drivers/cxl drivers/dax)'
    )
    parser.add_argument(
        '--author', type=str, default='Steve Scargall',
        help='Author name for Hugo front matter and podcast scripts (default: Steve Scargall)'
    )
    parser.add_argument(
        '--ai', action='store_true',
        help=(
            'Use Claude AI to generate enhanced prose. '
            'Uses claude CLI (subscription) or $ANTHROPIC_API_KEY as fallback.'
        )
    )
    parser.add_argument(
        '--ai-model', type=str, default='claude-sonnet-4-6',
        help='Claude model for AI generation via ANTHROPIC_API_KEY (default: claude-sonnet-4-6)'
    )
    return parser


def main(args):
    try:
        token = args.ghtoken or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        from_version = args.start_version
        to_version = args.end_version
        fmt = args.format

        # Require both versions or neither
        if bool(from_version) != bool(to_version):
            print(
                "Error: --start-version and --end-version must both be provided, or neither.",
                file=sys.stderr
            )
            sys.exit(1)

        # Formats that require --ai
        if fmt in ('podcast', 'video-short', 'explainers') and not args.ai:
            print(
                f"Error: --format {fmt} requires --ai. "
                f"Run with --ai to enable AI content generation.",
                file=sys.stderr
            )
            sys.exit(1)

        # Validate output directory/path before any API work
        if args.output and fmt != 'explainers':
            output_dir = os.path.dirname(os.path.abspath(args.output))
            if not os.path.isdir(output_dir):
                print(f"Error: output directory '{output_dir}' does not exist.", file=sys.stderr)
                sys.exit(1)

        tags = get_tags(token)

        if args.list_tags:
            if tags:
                if fmt == 'json':
                    print(json.dumps(tags, indent=4))
                else:
                    print("\n".join(tags))
            else:
                print("No tags found.")
            sys.exit(0)

        if not tags:
            print("No tags available, exiting.", file=sys.stderr)
            sys.exit(1)

        try:
            if from_version:
                from_version = validate_version(from_version, tags)
            if to_version:
                to_version = validate_version(to_version, tags)
        except ValueError as e:
            print(
                f"Error: {e}. Please ensure the specified versions exist in the repository tags.",
                file=sys.stderr
            )
            sys.exit(1)

        if not from_version or not to_version:
            from_version = tags[-2]
            to_version = tags[-1]

        commits = get_commits(from_version, to_version, token, args.paths)

        if not commits:
            print("No CXL related changes found.")
            return

        output = args.output

        # --- txt / md / json (unchanged) ---
        if fmt in ('txt', 'md', 'json', None) and fmt != 'hugo':
            if output:
                write_output(commits, output, fmt)
            else:
                print(f"\nCXL related changes from Kernel {from_version} to {to_version}:")
                if fmt == 'md':
                    for message, url in commits:
                        print(f"- [{message}]({url})")
                elif fmt == 'json':
                    print(json.dumps(commits, indent=4))
                elif args.verbose:
                    for message, url in commits:
                        print(f"- {message} ({url})")
                else:
                    print("- " + "\n- ".join(message for message, _ in commits))
            return

        # Categorize commits (used by hugo, podcast, video-short, explainers)
        categories = categorize_commits(commits)

        # --- hugo ---
        if fmt == 'hugo':
            if not output:
                output = f"{to_version}-cxl-changes.md"
                print(f"Writing Hugo post to {output}...", file=sys.stderr)
            # Use the kernel release date so the blog post date matches the release,
            # not the date the script was run.
            release_date = resolve_tag_date(to_version, token).split('T')[0]
            ai_content = None
            if args.ai:
                print("Generating AI-enhanced intro and key changes...", file=sys.stderr)
                ai_content = _generate_hugo_ai_content(
                    categories, from_version, to_version, args.ai_model
                )
                if not ai_content:
                    print("Warning: AI generation failed; proceeding with heuristic summary only.",
                          file=sys.stderr)
            write_hugo_output(commits, output, from_version, to_version, args.author,
                              categories=categories, ai_content=ai_content,
                              release_date=release_date)
            return

        # --- podcast ---
        if fmt == 'podcast':
            if not output:
                output = f"{to_version}-podcast-script.md"
            write_podcast_output(commits, categories, output, from_version, to_version,
                                 args.author, args.ai_model)
            return

        # --- video-short ---
        if fmt == 'video-short':
            if not output:
                output = f"{to_version}-video-short-script.md"
            write_video_short_output(commits, categories, output, from_version, to_version,
                                     args.ai_model)
            return

        # --- explainers ---
        if fmt == 'explainers':
            output_dir = output if output else f"{to_version}-explainers"
            write_explainers_output(commits, categories, output_dir, from_version, to_version,
                                    args.ai_model)
            return

    except KeyboardInterrupt:
        print("\nProcess interrupted by user.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    main(args)
