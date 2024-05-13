#!/usr/bin/env python3
import requests
import argparse
import json

def get_tags(token):
    """
    Fetches all tags from the Linux kernel GitHub repository and filters out release candidate tags.
    """
    url = "https://api.github.com/repos/torvalds/linux/tags"
    headers = {'Authorization': f'token {token}'} if token else {}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            tags = [tag['name'] for tag in response.json()]
            stable_tags = sorted([tag for tag in tags if "-rc" not in tag], key=lambda x: x.strip('v'))
            return stable_tags
        else:
            print(f"Failed to fetch tags: {response.status_code} {response.reason}")
            return []
    except requests.RequestException as e:
        print(f"Error fetching tags from GitHub: {e}")
        return []

def get_commits(from_tag, to_tag, token):
    """
    Fetches commits related to CXL from specified directories between two tags and includes URL links to each commit.
    """
    commits = []
    for path in ["drivers/cxl", "drivers/dax"]:
        url = f"https://api.github.com/repos/torvalds/linux/commits?sha={to_tag}&path={path}"
        headers = {'Authorization': f'token {token}'} if token else {}
        while url:
            try:
                response = requests.get(url, headers=headers)
                if response.status_code == 200:
                    page_commits = response.json()
                    if 'next' in response.links:
                        url = response.links['next']['url']
                    else:
                        url = None
                    for commit in page_commits:
                        if 'commit' in commit:
                            commit_message = commit['commit']['message'].split('\n')[0]  # Extract only the title
                            commit_url = commit['html_url']  # URL to the commit on GitHub
                            commits.append((commit_message, commit_url))
                else:
                    print(f"Failed to fetch commits: {response.status_code} {response.reason}")
                    break
            except requests.RequestException as e:
                print(f"Error fetching commits from GitHub: {e}")
                break
    return commits

def write_output(commits, output, format):
    """
    Writes commits to a file or prints to the terminal based on the user's choice of format, includes markdown link format.
    """
    try:
        if format == 'txt':
            with open(output, 'w') as file:
                for commit in commits:
                    file.write(commit[0] + '\n')  # Write only the commit message
        elif format == 'md':
            with open(output, 'w') as file:
                for commit_message, commit_url in commits:
                    file.write(f"- [{commit_message}]({commit_url})\n")  # Format as markdown link
        elif format == 'json':
            with open(output, 'w') as file:
                json.dump(commits, file, indent=4)  # Dump the entire list as JSON
    except IOError as e:
        print(f"Error writing to file {output}: {e}")

def main(args):
    """
    Main function to process the command-line arguments and initiate fetching and output of CXL changes.
    """
    token = args.ghtoken
    from_version = args.start_version
    to_version = args.end_version
    tags = get_tags(token)

    if not tags:
        print("No tags available, exiting.")
        return

    if not from_version or not to_version:
        from_version = tags[-2]
        to_version = tags[-1]

    commits = get_commits(from_version, to_version, token)
    
    if commits:
        if args.output:
            # If output file is specified, write to file according to the format
            write_output(commits, args.output, args.format)
        else:
            # No output file specified
            print(f"\nCXL related changes from Kernel {from_version} to {to_version}:")
            if args.format == 'md':
                # Output Markdown to STDOUT if format is 'md'
                for commit_message, commit_url in commits:
                    print(f"- [{commit_message}]({commit_url})")
            elif args.format == 'json':
                # Output JSON to STDOUT if format is 'json'
                print(json.dumps(commits, indent=4))
            elif args.verbose:
                # If verbose is specified, output detailed text to STDOUT
                for commit_message, commit_url in commits:
                    print(f"- {commit_message} ({commit_url})")
            else:
                # Default behavior, output only commit messages to STDOUT
                print("- " + "\n- ".join(commit_message for commit_message, commit_url in commits))
    else:
        print("No CXL related changes found.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track CXL feature changes in the Linux kernel.")
    parser.add_argument('--ghtoken', type=str, help='GitHub API token for authenticated requests')
    parser.add_argument('--start-version', type=str, help='Starting kernel version')
    parser.add_argument('--end-version', type=str, help='Ending kernel version')
    parser.add_argument('--output', type=str, help='Output file name (optional)')
    parser.add_argument('--format', choices=['txt', 'md', 'json'], default='default', help='Output format: txt, md, json, or default (terminal)')
    parser.add_argument('--verbose', action='store_true', help='Display detailed commit messages instead of just titles')
    args = parser.parse_args()
    main(args)

