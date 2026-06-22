#!/usr/bin/env python3
import requests
import argparse
import json
import sys
import os
import re
import time
import datetime

REPO = "torvalds/linux"
BASE_URL = f"https://api.github.com/repos/{REPO}"
DEFAULT_PATHS = ["drivers/cxl", "drivers/dax"]


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


def write_hugo_output(commits, output, from_version, to_version, author):
    """Write a complete Hugo blog post with YAML front matter."""
    today = datetime.date.today().isoformat()
    version_num = to_version.lstrip('v')

    front_matter = (
        f"---\n"
        f"title: \"Linux Kernel {to_version} is Released: This is What's New for Compute Express Link (CXL)\"\n"
        f"meta_title: \"\"\n"
        f"description: \"\"\n"
        f"date: {today}T00:00:00Z\n"
        f"image: \"featured_image.webp\"\n"
        f"categories: [\"CXL\"]\n"
        f"author: \"{author}\"\n"
        f"tags: [\"CXL\", \"Linux\", \"Kernel\"]\n"
        f"draft: false\n"
        f"aliases:\n"
        f"---\n"
        f"\n"
        f"The Linux Kernel {to_version} release brings several improvements and additions "
        f"related to Compute Express Link (CXL) technology.\n"
        f"\n"
        f"## CXL related changes from Kernel {from_version} to {to_version}\n"
        f"\n"
        f"Here is the detailed list of all commits merged into the {version_num} Kernel for CXL and DAX. "
        f"This list was generated by the "
        f"[Linux Kernel CXL Feature Tracker](https://github.com/sscargal/linux-cxl-tracker).\n"
        f"\n"
    )
    commit_list = ''.join(f"- [{msg}]({url})\n" for msg, url in commits)

    try:
        with open(output, 'w') as f:
            f.write(front_matter + commit_list)
    except IOError as e:
        print(f"Error: could not write to '{output}': {e}", file=sys.stderr)


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Track CXL feature changes in the Linux kernel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  GITHUB_TOKEN   GitHub personal access token (used if --ghtoken is not set)\n"
            "  GH_TOKEN       Alternative token variable name\n"
            "\n"
            "Examples:\n"
            "  %(prog)s --list-tags\n"
            "  %(prog)s --start-version v6.13 --end-version v6.14 --format md\n"
            "  %(prog)s --start-version v6.13 --end-version v6.14 --format hugo\n"
            "  %(prog)s --start-version v6.13 --end-version v6.14 --paths drivers/cxl include/linux/cxl\n"
        )
    )
    parser.add_argument(
        '--ghtoken', type=str,
        help='GitHub API token (or set $GITHUB_TOKEN / $GH_TOKEN)'
    )
    parser.add_argument('--start-version', type=str, help='Starting kernel version (e.g. v6.13)')
    parser.add_argument('--end-version', type=str, help='Ending kernel version (e.g. v6.14)')
    parser.add_argument('--output', type=str, help='Write output to this file (default: stdout)')
    parser.add_argument(
        '--format', choices=['txt', 'md', 'json', 'hugo'], default=None,
        help='Output format: txt, md, json, hugo (default: plain text to stdout)'
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
        help='Author name for Hugo front matter (default: Steve Scargall)'
    )
    return parser


def main(args):
    try:
        token = args.ghtoken or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        from_version = args.start_version
        to_version = args.end_version

        # Require both versions or neither
        if bool(from_version) != bool(to_version):
            print(
                "Error: --start-version and --end-version must both be provided, or neither.",
                file=sys.stderr
            )
            sys.exit(1)

        # Validate output directory exists before any API work
        if args.output:
            output_dir = os.path.dirname(os.path.abspath(args.output))
            if not os.path.isdir(output_dir):
                print(f"Error: output directory '{output_dir}' does not exist.", file=sys.stderr)
                sys.exit(1)

        tags = get_tags(token)

        if args.list_tags:
            if tags:
                if args.format == 'json':
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
        fmt = args.format

        if fmt == 'hugo':
            if not output:
                output = f"{to_version}-cxl-changes.md"
                print(f"Writing Hugo post to {output}...", file=sys.stderr)
            write_hugo_output(commits, output, from_version, to_version, args.author)
            return

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

    except KeyboardInterrupt:
        print("\nProcess interrupted by user.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    main(args)
