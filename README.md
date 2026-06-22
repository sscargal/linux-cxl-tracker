# Linux Kernel CXL Feature Tracker

## Description

This Python script tracks changes related to Compute Express Link (CXL) in the Linux kernel. It fetches commit messages from the Linux GitHub repository that are pertinent to CXL, specifically focusing on the `drivers/cxl` and `drivers/dax` directories. Users can specify the range of kernel versions to check, control the verbosity of the output, and decide on the output format and destination.

## Features

- **Graceful Interruption**: The script can be safely interrupted at any time by pressing Ctrl+C. It will stop processing and exit gracefully.
- **Hugo Blog Post Generation**: Generate a complete Hugo-formatted Markdown post with front matter in one command.
- **Configurable Paths**: Scan any kernel subsystem directory, not just the defaults.
- **Deduplicated Output**: Commits touching multiple tracked paths appear only once.

## Installation

### Prerequisites

- Python 3.8 or higher
- [uv](https://docs.astral.sh/uv/) — fast Python package and project manager

Install uv (if not already installed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Setup

```bash
# Clone the repository
git clone https://github.com/sscargal/linux-cxl-tracker
cd linux-cxl-tracker

# Install dependencies and create virtual environment
uv sync

# Verify the installation
uv run python3 cxl_feature_tracker.py --help
```

`uv sync` creates a `.venv/` directory and installs all dependencies automatically — no manual `pip install` or virtual environment activation needed.

### Updating packages

```bash
uv lock --upgrade   # update all packages to latest allowed versions
uv sync             # install the updated packages
```

### Adding new dependencies

```bash
uv add <package>          # add a runtime dependency
uv add --dev <package>    # add a development-only dependency
```

### Download

Clone this repository:

```bash
git clone https://github.com/sscargal/linux-cxl-tracker
```

## Usage

### Command-Line Options

| Option | Description |
|---|---|
| `--ghtoken TOKEN` | GitHub API token (or set `$GITHUB_TOKEN` / `$GH_TOKEN`) |
| `--start-version VER` | Starting kernel version (e.g. `v6.13`) |
| `--end-version VER` | Ending kernel version (e.g. `v6.14`) |
| `--output FILE` | Write output to this file (default: stdout) |
| `--format` | Output format: `txt`, `md`, `json`, `hugo` (default: plain text) |
| `--verbose` | Include commit URLs in terminal output |
| `--list-tags` | List all stable kernel tags and exit |
| `--paths PATH…` | Kernel repo paths to scan (default: `drivers/cxl drivers/dax`) |
| `--author NAME` | Author name for `--format hugo` front matter |

`--start-version` and `--end-version` must be provided together or not at all. When omitted, the two most recent stable releases are used automatically.

A GitHub personal access token is strongly recommended — unauthenticated requests are rate-limited to 60/hour. Set `export GITHUB_TOKEN=<your_token>` in your shell profile to avoid passing it on every invocation.

## Examples

Track changes with default options (latest two stable releases):
```bash
uv run python3 cxl_feature_tracker.py
```

List all stable kernel tags:
```bash
uv run python3 cxl_feature_tracker.py --list-tags
```

Track changes between specific versions, markdown output to stdout:
```bash
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 --format md
```

Generate a complete Hugo blog post in one step:
```bash
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 --format hugo
```

Write output to a file:
```bash
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 --format md --output changes.md
```

Verbose output (includes commit URLs):
```bash
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 --verbose
```

Using a GitHub token:
```bash
uv run python3 cxl_feature_tracker.py --ghtoken YOUR_GITHUB_TOKEN --start-version v6.13 --end-version v6.14
```

JSON output for further processing:
```bash
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 --format json --output changes.json
```

Scan additional kernel paths:
```bash
uv run python3 cxl_feature_tracker.py --start-version v6.13 --end-version v6.14 \
  --paths drivers/cxl drivers/dax include/linux/cxl --format md
```

## Running Tests

```bash
uv run pytest tests/ -v
```

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request with your improvements. See [CONTRIBUTING](CONTRIBUTING.md) for more information and instructions.

## License

This project is open source and available under the MIT License.
