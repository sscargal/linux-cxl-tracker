# Contributing to Linux Kernel CXL Feature Tracker

Thank you for your interest in contributing to the Linux Kernel CXL Feature Tracker! We welcome contributions from everyone, and we hope our guidelines make the process as smooth as possible for everyone involved.

## How to Contribute

Contributing to this project can be done in several ways. Below are some guidelines to help you get started:

## Reporting Issues

If you find a bug or have a suggestion for improving the documentation or code:

- Check the Issue Tracker to see if your issue has already been reported.
- If not, create a new issue with a detailed description:
  - Explain the expected behavior and the actual behavior.
  - Provide steps to reproduce the issue.
  - Mention the version of the software you are using (if applicable).
  - Include the output from the script, if applicable.

## Submitting Changes or New Features

If you'd like to submit a change to the repository:

1. Fork the repository.
2. Clone your fork:
```bash
git clone https://github.com/sscargal/linux-cxl-tracker
```
3. Create a new branch for your changes:
```.bash
git checkout -b feature-branch-name
```
4. Make your changes and commit them using a signed commit. To create a certificate for signed commits and configure `git`, follow the instructions to install and configure [GitSign GitStore](https://docs.sigstore.dev/signing/gitsign/), then:
```bash
git commit -am "Add some feature" -S
```

5. Push the changes to your fork:
```bash
git push origin feature-branch-name
```
6. Submit a pull request from your feature branch to the original repo's main branch.

## Pull Request Guidelines

Ensure your code adheres to the existing style so that it's as readable and maintainable as possible.

Update the README.md with details of changes, including new environment variables, exposed ports, useful file locations, and container parameters.

Increase the version numbers in any examples files and the README.md to the new version that this Pull Request would represent.

You may merge the Pull Request in once you have the sign-off of two other developers, or if you do not have permission to do that, you may request the second reviewer to merge it for you.

## Development Setup

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
4. Run the script with a test command to ensure it's working:
```bash
./cxl_feature_tracker.py --start-version v5.8 --end-version v5.9 --verbose
```

## Community

We want to maintain a welcoming and respectful community. Whether you are a newcomer or a long-time contributor, we encourage you to follow the Community Code of Conduct.

Thank you for contributing to the Linux Kernel CXL Feature Tracker. We look forward to your contributions!