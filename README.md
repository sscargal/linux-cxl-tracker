# Linux Kernel CXL Feature Tracker

## Description

This Python script tracks changes related to Compute Express Link (CXL) in the Linux kernel. It fetches commit messages from the Linux GitHub repository that are pertinent to CXL, specifically focusing on the drivers/cxl and drivers/dax directories. Users can specify the range of kernel versions to check, control the verbosity of the output, and decide on the output format and destination.

## Installation

### Prerequisites

- Python 3.6 or higher
- Python requests library
- `venv` or `conda` Python Virtual Environment (Optional)

### Setup

Here's a quick setup guide if you want to run the script locally:

1. Install Python 3.x.
2. Create a Virtual Environment. It's a good practice to use a virtual environment for Python projects to manage dependencies separately from the global Python environment. You can create a virtual environment using `venv` or `conda`:

Using `venv`
```bash
python -m venv env
source env/bin/activate  # On Windows use env\Scripts\activate
```
Using `Conda`
```bash
conda create --name myenv python=3.8
conda activate myenv
```
3. Install the required dependencies:
```bash
pip install -r requirements.txt
```

### Download

Clone this repository 

```bash
git clone https://github.com/sscargal/linux-cxl-tracker
```

or download the script directly:

```bash
git clone https://github.com/sscargal/linux-cxl-tracker/linux-cxl-tracker.git
```

## Usage

Command-Line Options

- `--ghtoken`: Specifies the GitHub API token for authenticated requests (optional but recommended to avoid rate limits).
- `--start-version`: Specifies the starting kernel version to track changes from.
- `--end-version`: Specifies the ending kernel version to track changes to.
- `--output`: Specifies the filename where the output should be written. If not provided, output is printed to the terminal.
- `--format`: Specifies the format of the output file. Options include txt, md, json. Default is terminal output.
- `--verbose`: When set, the script will display detailed commit messages. By default, only commit titles are shown.

## Examples

Track Changes with Default Options:
```bash
./cxl_feature_tracker.py --start-version v5.8 --end-version v5.9
```

Track Changes with Verbose Output:
```bash
./cxl_feature_tracker.py --start-version v5.8 --end-version v5.9 --verbose
```

Track Changes and Write to a Text File:
```bash
./cxl_feature_tracker.py --start-version v5.8 --end-version v5.9 --output changes.txt --format txt
```

Track Changes and Write to a Markdown File with Verbose Output:
```bash
./cxl_feature_tracker.py --start-version v5.8 --end-version v5.9 --output changes.md --format md --verbose
```

Track Changes Using GitHub Token:
GitHub API has a rate limit, especially for unauthenticated requests (default). For more frequent usage or large data, use authentication by adding a token in the request headers.
```bash
./cxl_feature_tracker.py --ghtoken YOUR_GITHUB_TOKEN --start-version v5.8 --end-version v5.9
```

Track Changes and Write to a JSON File:
```bash
./cxl_feature_tracker.py --start-version v5.8 --end-version v5.9 --output changes.json --format json
```

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request with your improvements. See [CONTRIBUTING](CONTRIBUTING.md) for more information and instructions.

## License

This project is open source and available under the MIT License.