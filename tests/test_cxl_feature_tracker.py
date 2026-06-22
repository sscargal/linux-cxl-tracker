"""
Tests for cxl_feature_tracker.py.

All tests are fully offline — no real GitHub token or network access required.
HTTP calls are intercepted via unittest.mock.patch on requests.get.
"""
import argparse
import json
import os
import sys
import time as time_module

import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cxl_feature_tracker as tracker

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_TAGS_RAW = [
    {"name": "v6.14"},
    {"name": "v6.13"},
    {"name": "v6.10"},
    {"name": "v6.9"},
    {"name": "v6.9-rc1"},
    {"name": "v5.15"},
]

SORTED_STABLE = ["v5.15", "v6.9", "v6.10", "v6.13", "v6.14"]

TAG_DATE_RESPONSE = {
    "sha": "tagsha000",
    "commit": {
        "committer": {"date": "2024-01-15T00:00:00Z"}
    },
}

COMMITS_PATH1 = [
    {
        "sha": "aaa111",
        "commit": {"message": "cxl: fix thing\n\nLong description"},
        "html_url": "https://github.com/torvalds/linux/commit/aaa111",
    },
    {
        "sha": "bbb222",
        "commit": {"message": "cxl: another fix"},
        "html_url": "https://github.com/torvalds/linux/commit/bbb222",
    },
]

COMMITS_PATH2 = [
    # aaa111 is a duplicate (appears in both paths)
    {
        "sha": "aaa111",
        "commit": {"message": "cxl: fix thing\n\nLong description"},
        "html_url": "https://github.com/torvalds/linux/commit/aaa111",
    },
    {
        "sha": "ccc333",
        "commit": {"message": "dax: update handler"},
        "html_url": "https://github.com/torvalds/linux/commit/ccc333",
    },
]


def make_response(data, status=200, links=None, headers=None):
    """Build a mock requests.Response."""
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.links = links or {}
    m.reason = {
        200: "OK", 403: "Forbidden", 404: "Not Found",
        429: "Too Many Requests", 500: "Internal Server Error",
    }.get(status, "Unknown")
    m.headers = headers or {
        'X-RateLimit-Remaining': '4999',
        'X-RateLimit-Reset': str(int(time_module.time()) + 3600),
    }
    return m


def make_args(**kwargs):
    """Build an argparse.Namespace with sensible defaults for main()."""
    defaults = {
        'ghtoken': None,
        'start_version': None,
        'end_version': None,
        'output': None,
        'format': None,
        'verbose': False,
        'list_tags': False,
        'paths': list(tracker.DEFAULT_PATHS),
        'author': 'Steve Scargall',
        'ai': False,
        'ai_model': 'claude-sonnet-4-6',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _make_headers
# ---------------------------------------------------------------------------

class TestMakeHeaders:
    def test_with_token(self):
        h = tracker._make_headers("mytoken")
        assert h == {'Authorization': 'token mytoken'}

    def test_without_token(self):
        assert tracker._make_headers(None) == {}

    def test_empty_string_token(self):
        assert tracker._make_headers("") == {}


# ---------------------------------------------------------------------------
# get_tags
# ---------------------------------------------------------------------------

class TestGetTags:
    def test_filters_rc_tags(self):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            tags = tracker.get_tags(None)
        assert "v6.9-rc1" not in tags

    def test_returns_only_stable_tags(self):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            tags = tracker.get_tags(None)
        assert tags == SORTED_STABLE

    def test_v6_9_before_v6_10(self):
        """BUG-5: lexicographic sort would fail here."""
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            tags = tracker.get_tags(None)
        assert tags.index("v6.9") < tags.index("v6.10")

    def test_v6_10_before_v6_13(self):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            tags = tracker.get_tags(None)
        assert tags.index("v6.10") < tags.index("v6.13")

    def test_http_error_returns_empty(self):
        with patch('requests.get', return_value=make_response([], status=500)):
            tags = tracker.get_tags(None)
        assert tags == []

    def test_404_returns_empty(self):
        with patch('requests.get', return_value=make_response([], status=404)):
            tags = tracker.get_tags(None)
        assert tags == []

    def test_network_error_returns_empty(self):
        with patch('requests.get', side_effect=requests.RequestException("timeout")):
            tags = tracker.get_tags(None)
        assert tags == []

    def test_pagination_followed(self):
        page1 = make_response(
            [{"name": "v6.14"}],
            links={"next": {"url": "https://api.github.com/repos/torvalds/linux/tags?page=2"}}
        )
        page2 = make_response([{"name": "v6.13"}])
        with patch('requests.get', side_effect=[page1, page2]):
            tags = tracker.get_tags(None)
        assert "v6.14" in tags
        assert "v6.13" in tags

    def test_token_sent_in_auth_header(self):
        with patch('requests.get', return_value=make_response([])) as mock_get:
            tracker.get_tags("mytoken")
        headers = mock_get.call_args[1]['headers']
        assert headers.get('Authorization') == 'token mytoken'

    def test_no_token_sends_empty_headers(self):
        with patch('requests.get', return_value=make_response([])) as mock_get:
            tracker.get_tags(None)
        headers = mock_get.call_args[1]['headers']
        assert headers == {}

    def test_rate_limit_403_exits(self):
        with patch('requests.get', return_value=make_response({}, status=403)):
            with pytest.raises(SystemExit) as exc:
                tracker.get_tags(None)
        assert exc.value.code == 1

    def test_rate_limit_429_exits(self):
        with patch('requests.get', return_value=make_response({}, status=429)):
            with pytest.raises(SystemExit) as exc:
                tracker.get_tags(None)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# resolve_tag_date
# ---------------------------------------------------------------------------

class TestResolveTagDate:
    def test_returns_date_string(self):
        with patch('requests.get', return_value=make_response(TAG_DATE_RESPONSE)):
            date = tracker.resolve_tag_date("v6.13", "token")
        assert date == "2024-01-15T00:00:00Z"

    def test_exits_on_404(self):
        with patch('requests.get', return_value=make_response({"message": "Not Found"}, status=404)):
            with pytest.raises(SystemExit) as exc:
                tracker.resolve_tag_date("vbad", "token")
        assert exc.value.code == 1

    def test_exits_on_network_error(self):
        with patch.object(tracker, '_api_get', return_value=None):
            with pytest.raises(SystemExit) as exc:
                tracker.resolve_tag_date("v6.13", "token")
        assert exc.value.code == 1

    def test_exits_on_malformed_json(self):
        bad = {"no_commit_key": True}
        with patch('requests.get', return_value=make_response(bad)):
            with pytest.raises(SystemExit) as exc:
                tracker.resolve_tag_date("v6.13", "token")
        assert exc.value.code == 1

    def test_request_url_contains_tag(self):
        with patch('requests.get', return_value=make_response(TAG_DATE_RESPONSE)) as mock_get:
            tracker.resolve_tag_date("v6.13", "token")
        url = mock_get.call_args[0][0]
        assert "v6.13" in url


# ---------------------------------------------------------------------------
# get_commits
# ---------------------------------------------------------------------------

class TestGetCommits:
    def _tag_date_resp(self):
        return make_response(TAG_DATE_RESPONSE)

    def test_deduplicates_across_paths(self):
        """BUG-6: sha aaa111 appears in both paths; should appear only once."""
        responses = [self._tag_date_resp(), make_response(COMMITS_PATH1), make_response(COMMITS_PATH2)]
        with patch('requests.get', side_effect=responses):
            commits = tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl", "drivers/dax"])
        shas = [url.split('/')[-1] for _, url in commits]
        assert len(shas) == len(set(shas)), "Duplicate commit URLs found"
        assert len(commits) == 3  # aaa111, bbb222, ccc333

    def test_since_param_in_commits_url(self):
        """BUG-1: commits URL must include since= derived from from_tag's date."""
        tag_resp = self._tag_date_resp()
        commits_resp = make_response([])
        with patch('requests.get', side_effect=[tag_resp, commits_resp]) as mock_get:
            tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        commits_url = mock_get.call_args_list[1][0][0]
        assert "since=2024-01-15T00:00:00Z" in commits_url

    def test_to_tag_in_commits_url(self):
        tag_resp = self._tag_date_resp()
        commits_resp = make_response([])
        with patch('requests.get', side_effect=[tag_resp, commits_resp]) as mock_get:
            tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        commits_url = mock_get.call_args_list[1][0][0]
        assert "sha=v6.14" in commits_url

    def test_extracts_first_line_only(self):
        """Commit messages with multi-line bodies should only use the subject."""
        commit_data = [{
            "sha": "abc",
            "commit": {"message": "cxl: fix bug\n\nThis is the body paragraph."},
            "html_url": "https://github.com/t/l/commit/abc",
        }]
        responses = [self._tag_date_resp(), make_response(commit_data)]
        with patch('requests.get', side_effect=responses):
            commits = tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        assert commits[0][0] == "cxl: fix bug"

    def test_pagination_followed_per_path(self):
        page1 = make_response(
            COMMITS_PATH1[:1],
            links={"next": {"url": "https://api.github.com/page2"}}
        )
        page2 = make_response(COMMITS_PATH1[1:])
        responses = [self._tag_date_resp(), page1, page2]
        with patch('requests.get', side_effect=responses):
            commits = tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        assert len(commits) == 2

    def test_http_error_returns_partial_results(self):
        responses = [self._tag_date_resp(), make_response([], status=500)]
        with patch('requests.get', side_effect=responses):
            commits = tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        assert commits == []

    def test_rate_limit_exits(self):
        responses = [self._tag_date_resp(), make_response({}, status=429)]
        with patch('requests.get', side_effect=responses):
            with pytest.raises(SystemExit) as exc:
                tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        assert exc.value.code == 1

    def test_network_error_returns_empty(self):
        tag_resp = self._tag_date_resp()
        with patch('requests.get', side_effect=[tag_resp, requests.RequestException("err")]):
            commits = tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        assert commits == []

    def test_custom_paths_used_in_url(self):
        tag_resp = self._tag_date_resp()
        commits_resp = make_response([])
        with patch('requests.get', side_effect=[tag_resp, commits_resp]) as mock_get:
            tracker.get_commits("v6.13", "v6.14", "token", ["include/linux/cxl"])
        commits_url = mock_get.call_args_list[1][0][0]
        assert "path=include/linux/cxl" in commits_url

    def test_default_paths_used_when_none(self):
        tag_resp = self._tag_date_resp()
        # Two paths → two commits API calls
        resp1 = make_response([])
        resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, resp1, resp2]) as mock_get:
            tracker.get_commits("v6.13", "v6.14", "token")
        # Calls: [resolve_tag_date, drivers/cxl, drivers/dax]
        assert mock_get.call_count == 3

    def test_malformed_commit_entry_skipped(self):
        bad_commits = [
            {"sha": "aaa", "commit": {"message": "ok"}, "html_url": "http://x"},
            {"sha": "bbb"},  # missing commit/html_url
        ]
        responses = [self._tag_date_resp(), make_response(bad_commits)]
        with patch('requests.get', side_effect=responses):
            commits = tracker.get_commits("v6.13", "v6.14", "token", ["drivers/cxl"])
        assert len(commits) == 1
        assert commits[0][0] == "ok"


# ---------------------------------------------------------------------------
# validate_version
# ---------------------------------------------------------------------------

class TestValidateVersion:
    TAGS = ["v5.15", "v6.9", "v6.10", "v6.13", "v6.14"]

    def test_adds_v_prefix(self):
        assert tracker.validate_version("6.14", self.TAGS) == "v6.14"

    def test_keeps_existing_v_prefix(self):
        assert tracker.validate_version("v6.14", self.TAGS) == "v6.14"

    def test_raises_for_unknown_version(self):
        with pytest.raises(ValueError, match="Invalid version"):
            tracker.validate_version("v99.99", self.TAGS)

    def test_raises_for_rc_not_in_tags(self):
        with pytest.raises(ValueError):
            tracker.validate_version("v6.9-rc1", self.TAGS)


# ---------------------------------------------------------------------------
# write_output
# ---------------------------------------------------------------------------

class TestWriteOutput:
    COMMITS = [
        ("cxl: fix thing", "https://github.com/t/l/commit/abc"),
        ("dax: update handler", "https://github.com/t/l/commit/def"),
    ]

    def test_txt_format_writes_titles_only(self, tmp_path):
        out = str(tmp_path / "out.txt")
        tracker.write_output(self.COMMITS, out, 'txt')
        content = open(out).read()
        assert "cxl: fix thing\n" in content
        assert "https://" not in content

    def test_md_format_writes_links(self, tmp_path):
        out = str(tmp_path / "out.md")
        tracker.write_output(self.COMMITS, out, 'md')
        content = open(out).read()
        assert "- [cxl: fix thing](https://github.com/t/l/commit/abc)" in content

    def test_json_format_writes_valid_json(self, tmp_path):
        out = str(tmp_path / "out.json")
        tracker.write_output(self.COMMITS, out, 'json')
        data = json.loads(open(out).read())
        assert isinstance(data, list)
        assert data[0][0] == "cxl: fix thing"
        assert data[0][1] == "https://github.com/t/l/commit/abc"

    def test_none_format_writes_plain_text_not_empty(self, tmp_path):
        """BUG-4: format=None (the default) must produce non-empty output."""
        out = str(tmp_path / "out.txt")
        tracker.write_output(self.COMMITS, out, None)
        content = open(out).read()
        assert len(content) > 0
        assert "cxl: fix thing" in content

    def test_ioerror_is_caught_not_raised(self, capsys):
        tracker.write_output(self.COMMITS, "/nonexistent/dir/out.txt", 'txt')
        captured = capsys.readouterr()
        assert "Error" in captured.err


# ---------------------------------------------------------------------------
# write_hugo_output
# ---------------------------------------------------------------------------

class TestWriteHugoOutput:
    COMMITS = [
        ("cxl: fix thing", "https://github.com/t/l/commit/abc"),
        ("dax: update", "https://github.com/t/l/commit/def"),
    ]

    def _write(self, tmp_path, author="Test Author", from_v="v6.13", to_v="v6.14"):
        out = str(tmp_path / "post.md")
        tracker.write_hugo_output(self.COMMITS, out, from_v, to_v, author)
        return open(out).read()

    def test_starts_with_front_matter_delimiter(self, tmp_path):
        content = self._write(tmp_path)
        assert content.startswith("---\n")

    def test_title_contains_to_version(self, tmp_path):
        content = self._write(tmp_path)
        assert "v6.14" in content

    def test_author_in_front_matter(self, tmp_path):
        content = self._write(tmp_path, author="Jane Doe")
        assert 'author: "Jane Doe"' in content

    def test_draft_false(self, tmp_path):
        content = self._write(tmp_path)
        assert "draft: false" in content

    def test_categories_cxl(self, tmp_path):
        content = self._write(tmp_path)
        assert 'categories: ["CXL"]' in content

    def test_commit_list_as_markdown_links(self, tmp_path):
        content = self._write(tmp_path)
        assert "- [cxl: fix thing](https://github.com/t/l/commit/abc)" in content

    def test_both_versions_in_body(self, tmp_path):
        content = self._write(tmp_path)
        assert "v6.13" in content
        assert "v6.14" in content

    def test_ioerror_caught_not_raised(self, capsys):
        tracker.write_hugo_output(self.COMMITS, "/nonexistent/dir/post.md", "v6.13", "v6.14", "Author")
        assert "Error" in capsys.readouterr().err

    def test_version_in_section_heading(self, tmp_path):
        content = self._write(tmp_path)
        assert "Kernel" in content and "v6.14" in content

    def test_with_categories_shows_stats_table(self, tmp_path):
        out = str(tmp_path / "post.md")
        categories = tracker.categorize_commits(self.COMMITS)
        tracker.write_hugo_output(self.COMMITS, out, "v6.13", "v6.14", "Test",
                                  categories=categories)
        content = open(out).read()
        assert "| Category | Commits |" in content

    def test_with_ai_content_included(self, tmp_path):
        out = str(tmp_path / "post.md")
        tracker.write_hugo_output(self.COMMITS, out, "v6.13", "v6.14", "Test",
                                  ai_content="## Key Changes\n- item one\n")
        content = open(out).read()
        assert "Key Changes" in content


# ---------------------------------------------------------------------------
# main() — integration
# ---------------------------------------------------------------------------

class TestMain:
    """Test main() using argparse.Namespace to simulate parsed CLI args."""

    # -- list-tags --

    def test_list_tags_exits_zero(self, capsys):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            with pytest.raises(SystemExit) as exc:
                tracker.main(make_args(list_tags=True))
        assert exc.value.code == 0

    def test_list_tags_prints_stable_sorted(self, capsys):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            with pytest.raises(SystemExit):
                tracker.main(make_args(list_tags=True))
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert "v6.9-rc1" not in lines
        assert lines.index("v6.9") < lines.index("v6.10")

    def test_list_tags_json_format(self, capsys):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            with pytest.raises(SystemExit):
                tracker.main(make_args(list_tags=True, format='json'))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert "v6.9-rc1" not in data

    # -- token resolution --

    def test_env_github_token_used_when_no_ghtoken(self):
        responses = [make_response(SAMPLE_TAGS_RAW)]
        with patch('requests.get', side_effect=responses) as mock_get:
            with patch.dict(os.environ, {'GITHUB_TOKEN': 'env_token'}, clear=False):
                with pytest.raises(SystemExit):
                    tracker.main(make_args(list_tags=True, ghtoken=None))
        headers = mock_get.call_args[1]['headers']
        assert 'env_token' in headers.get('Authorization', '')

    def test_gh_token_env_var_fallback(self):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)) as mock_get:
            with patch.dict(os.environ, {'GH_TOKEN': 'gh_env_token'}, clear=False):
                # Remove GITHUB_TOKEN from env for this test
                env = {k: v for k, v in os.environ.items() if k != 'GITHUB_TOKEN'}
                env['GH_TOKEN'] = 'gh_env_token'
                with patch.dict(os.environ, env, clear=True):
                    with pytest.raises(SystemExit):
                        tracker.main(make_args(list_tags=True, ghtoken=None))
        headers = mock_get.call_args[1]['headers']
        assert 'gh_env_token' in headers.get('Authorization', '')

    def test_ghtoken_takes_priority_over_env(self):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)) as mock_get:
            with patch.dict(os.environ, {'GITHUB_TOKEN': 'env_token'}, clear=False):
                with pytest.raises(SystemExit):
                    tracker.main(make_args(list_tags=True, ghtoken='cli_token'))
        headers = mock_get.call_args[1]['headers']
        assert 'cli_token' in headers.get('Authorization', '')

    def test_no_token_sends_no_auth_header(self):
        env = {k: v for k, v in os.environ.items() if k not in ('GITHUB_TOKEN', 'GH_TOKEN')}
        with patch.dict(os.environ, env, clear=True):
            with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)) as mock_get:
                with pytest.raises(SystemExit):
                    tracker.main(make_args(list_tags=True, ghtoken=None))
        headers = mock_get.call_args[1]['headers']
        assert 'Authorization' not in headers

    # -- version validation --

    def test_only_start_version_exits_with_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tracker.main(make_args(start_version='v6.13'))
        assert exc.value.code == 1
        assert "Error" in capsys.readouterr().err

    def test_only_end_version_exits_with_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tracker.main(make_args(end_version='v6.14'))
        assert exc.value.code == 1

    def test_invalid_version_exits(self, capsys):
        with patch('requests.get', return_value=make_response(SAMPLE_TAGS_RAW)):
            with pytest.raises(SystemExit) as exc:
                tracker.main(make_args(start_version='v99.99', end_version='v100.0'))
        assert exc.value.code == 1
        assert "Error" in capsys.readouterr().err

    def test_default_picks_last_two_stable_tags(self, capsys):
        """BUG-5: default must pick v6.13 and v6.14, not v6.14 and v6.9 due to bad sorting."""
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args())
        out = capsys.readouterr().out
        assert "v6.13" in out
        assert "v6.14" in out

    # -- output routing --

    def test_output_to_file_txt(self, tmp_path):
        out = str(tmp_path / "out.txt")
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                output=out, format='txt'
            ))
        content = open(out).read()
        assert "cxl: fix thing" in content

    def test_output_without_format_writes_plain_text(self, tmp_path):
        """BUG-3/4: --output without --format must produce non-empty file."""
        out = str(tmp_path / "out.txt")
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                output=out, format=None
            ))
        content = open(out).read()
        assert len(content) > 0
        assert "cxl: fix thing" in content

    def test_nonexistent_output_dir_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                output='/nonexistent/dir/out.txt'
            ))
        assert exc.value.code == 1
        assert "Error" in capsys.readouterr().err

    # -- stdout output modes --

    def test_stdout_default_format(self, capsys):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args(start_version='v6.13', end_version='v6.14'))
        out = capsys.readouterr().out
        assert "cxl: fix thing" in out
        assert "http" not in out  # no URLs in default non-verbose mode

    def test_stdout_verbose_includes_urls(self, capsys):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args(start_version='v6.13', end_version='v6.14', verbose=True))
        out = capsys.readouterr().out
        assert "https://github.com" in out

    def test_stdout_md_format(self, capsys):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args(start_version='v6.13', end_version='v6.14', format='md'))
        out = capsys.readouterr().out
        assert "- [cxl: fix thing](" in out

    def test_stdout_json_format(self, capsys):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args(start_version='v6.13', end_version='v6.14', format='json'))
        out = capsys.readouterr().out
        # stdout contains a header line then the JSON array; find the JSON portion
        json_start = out.index('[')
        data = json.loads(out[json_start:])
        assert isinstance(data, list)

    # -- hugo output --

    def test_hugo_format_writes_front_matter(self, tmp_path):
        out = str(tmp_path / "post.md")
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)    # resolve_tag_date(from_tag)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        to_date_resp = make_response(TAG_DATE_RESPONSE)     # resolve_tag_date(to_tag)
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2, to_date_resp]):
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='hugo', output=out
            ))
        content = open(out).read()
        assert content.startswith("---\n")
        assert "draft: false" in content

    def test_hugo_date_uses_release_date_not_today(self, tmp_path):
        """The date: field must be the kernel release date, not today."""
        out = str(tmp_path / "post.md")
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        to_date_resp = make_response(TAG_DATE_RESPONSE)
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2, to_date_resp]):
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='hugo', output=out
            ))
        content = open(out).read()
        # TAG_DATE_RESPONSE returns "2024-01-15T00:00:00Z"; date: line must use that date
        assert "date: 2024-01-15T00:00:00Z" in content

    def test_hugo_default_filename_when_no_output(self, tmp_path, capsys):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        to_date_resp = make_response(TAG_DATE_RESPONSE)
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2, to_date_resp]):
                tracker.main(make_args(
                    start_version='v6.13', end_version='v6.14',
                    format='hugo', output=None
                ))
            assert os.path.exists("v6.14-cxl-changes.md")
        finally:
            os.chdir(original_dir)

    def test_hugo_custom_author(self, tmp_path):
        out = str(tmp_path / "post.md")
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        to_date_resp = make_response(TAG_DATE_RESPONSE)
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2, to_date_resp]):
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='hugo', output=out, author='Jane Doe'
            ))
        content = open(out).read()
        assert 'author: "Jane Doe"' in content

    # -- custom paths --

    def test_custom_paths_passed_to_get_commits(self):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp]) as mock_get:
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                paths=['include/linux/cxl']
            ))
        # Third call is the commits API for the custom path
        commits_url = mock_get.call_args_list[2][0][0]
        assert "path=include/linux/cxl" in commits_url

    # -- keyboard interrupt --

    def test_keyboard_interrupt_exits_cleanly(self, capsys):
        """BUG-2: KeyboardInterrupt must not raise NameError due to missing sys import."""
        with patch('requests.get', side_effect=KeyboardInterrupt()):
            with pytest.raises(SystemExit) as exc:
                tracker.main(make_args(list_tags=True))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "interrupted" in err.lower()

    # -- no commits found --

    def test_no_commits_prints_message(self, capsys):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        empty1 = make_response([])
        empty2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, empty1, empty2]):
            tracker.main(make_args(start_version='v6.13', end_version='v6.14'))
        assert "No CXL related changes found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _build_parser — argparse configuration
# ---------------------------------------------------------------------------

class TestBuildParser:
    def setup_method(self):
        self.parser = tracker._build_parser()

    def test_format_choices_include_hugo(self):
        args = self.parser.parse_args(['--format', 'hugo'])
        assert args.format == 'hugo'

    def test_format_default_is_none(self):
        """BUG-3: default must be None, not the string 'default'."""
        args = self.parser.parse_args([])
        assert args.format is None

    def test_format_invalid_rejected(self):
        with pytest.raises(SystemExit):
            self.parser.parse_args(['--format', 'xml'])

    def test_paths_default(self):
        args = self.parser.parse_args([])
        assert args.paths == list(tracker.DEFAULT_PATHS)

    def test_paths_custom(self):
        args = self.parser.parse_args(['--paths', 'drivers/cxl', 'include/linux/cxl'])
        assert 'drivers/cxl' in args.paths
        assert 'include/linux/cxl' in args.paths

    def test_author_default(self):
        args = self.parser.parse_args([])
        assert args.author == 'Steve Scargall'

    def test_author_custom(self):
        args = self.parser.parse_args(['--author', 'Jane Doe'])
        assert args.author == 'Jane Doe'

    def test_list_tags_flag(self):
        args = self.parser.parse_args(['--list-tags'])
        assert args.list_tags is True

    def test_verbose_flag(self):
        args = self.parser.parse_args(['--verbose'])
        assert args.verbose is True

    def test_ai_flag_default_false(self):
        args = self.parser.parse_args([])
        assert args.ai is False

    def test_ai_flag_set(self):
        args = self.parser.parse_args(['--ai'])
        assert args.ai is True

    def test_ai_model_default(self):
        args = self.parser.parse_args([])
        assert args.ai_model == 'claude-sonnet-4-6'

    def test_ai_model_custom(self):
        args = self.parser.parse_args(['--ai-model', 'claude-opus-4-8'])
        assert args.ai_model == 'claude-opus-4-8'

    def test_format_choices_include_podcast(self):
        args = self.parser.parse_args(['--format', 'podcast'])
        assert args.format == 'podcast'

    def test_format_choices_include_video_short(self):
        args = self.parser.parse_args(['--format', 'video-short'])
        assert args.format == 'video-short'

    def test_format_choices_include_explainers(self):
        args = self.parser.parse_args(['--format', 'explainers'])
        assert args.format == 'explainers'


# ---------------------------------------------------------------------------
# categorize_commits
# ---------------------------------------------------------------------------

class TestCategorizeCommits:
    def test_fix_commit_goes_to_bug_fixes(self):
        commits = [("cxl: fix memory leak", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: fix memory leak", "http://x") in cats["Bug Fixes"]

    def test_add_support_goes_to_features(self):
        commits = [("cxl: add support for new device", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: add support for new device", "http://x") in cats["New Features & Hardware"]

    def test_refactor_goes_to_cleanup(self):
        commits = [("cxl: refactor region handling", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: refactor region handling", "http://x") in cats["Refactoring & Cleanup"]

    def test_test_commit_goes_to_testing(self):
        commits = [("cxl: add selftests for region", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: add selftests for region", "http://x") in cats["Testing"]

    def test_doc_commit_goes_to_documentation(self):
        commits = [("cxl: update documentation for HDM decoder", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: update documentation for HDM decoder", "http://x") in cats["Documentation"]

    def test_perf_commit_goes_to_performance(self):
        commits = [("cxl: optimiz interrupt path for lower latency", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: optimiz interrupt path for lower latency", "http://x") in cats["Performance"]

    def test_unrecognized_goes_to_other(self):
        commits = [("cxl: miscellaneous tweak", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: miscellaneous tweak", "http://x") in cats["Other"]

    def test_all_commits_assigned_exactly_once(self):
        commits = [
            ("cxl: fix thing", "http://a"),
            ("cxl: add support for X", "http://b"),
            ("cxl: refactor Y", "http://c"),
            ("cxl: weird commit", "http://d"),
        ]
        cats = tracker.categorize_commits(commits)
        all_assigned = [c for cat_list in cats.values() for c in cat_list]
        assert len(all_assigned) == len(commits)
        assert len(set(url for _, url in all_assigned)) == len(commits)

    def test_empty_commits_returns_empty_categories(self):
        cats = tracker.categorize_commits([])
        assert all(len(v) == 0 for v in cats.values())

    def test_returns_all_category_keys(self):
        cats = tracker.categorize_commits([])
        for cat in tracker.CATEGORY_ORDER:
            assert cat in cats

    def test_first_match_wins(self):
        # "add support" matches Features; if also "test" in msg, should still be Features
        commits = [("cxl: add support for testing environment", "http://x")]
        cats = tracker.categorize_commits(commits)
        assert ("cxl: add support for testing environment", "http://x") in cats["New Features & Hardware"]
        assert ("cxl: add support for testing environment", "http://x") not in cats["Testing"]


# ---------------------------------------------------------------------------
# build_ai_context
# ---------------------------------------------------------------------------

class TestBuildAiContext:
    COMMITS = [
        ("cxl: fix thing", "http://a"),
        ("cxl: add support for X", "http://b"),
    ]

    def test_includes_version_info(self):
        cats = tracker.categorize_commits(self.COMMITS)
        ctx = tracker.build_ai_context(cats, "v6.13", "v6.14")
        assert "v6.13" in ctx
        assert "v6.14" in ctx

    def test_includes_total_count(self):
        cats = tracker.categorize_commits(self.COMMITS)
        ctx = tracker.build_ai_context(cats, "v6.13", "v6.14")
        assert "2" in ctx

    def test_includes_category_names(self):
        commits = [("cxl: fix bug", "http://a")]
        cats = tracker.categorize_commits(commits)
        ctx = tracker.build_ai_context(cats, "v6.13", "v6.14")
        assert "Bug Fixes" in ctx

    def test_max_per_cat_limits_commits(self):
        commits = [(f"cxl: fix thing {i}", f"http://{i}") for i in range(30)]
        cats = tracker.categorize_commits(commits)
        ctx = tracker.build_ai_context(cats, "v6.13", "v6.14", max_per_cat=5)
        # Only 5 commits per category should appear (30 bugs, 5 listed)
        # Count occurrences of "cxl: fix thing" lines
        lines_with_fix = [l for l in ctx.splitlines() if "cxl: fix thing" in l]
        assert len(lines_with_fix) <= 5


# ---------------------------------------------------------------------------
# call_ai
# ---------------------------------------------------------------------------

class TestCallAi:
    def test_uses_claude_cli_when_available(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "AI response text\n"
        mock_result.stderr = ""
        with patch('shutil.which', return_value='/usr/local/bin/claude'):
            with patch('subprocess.run', return_value=mock_result) as mock_run:
                result = tracker.call_ai("test prompt")
        assert result == "AI response text"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == 'claude'
        assert '-p' in cmd
        assert 'test prompt' in cmd

    def test_claude_cli_failure_falls_back_to_sdk(self):
        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        fail_result.stderr = "error"
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="SDK response")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        with patch('shutil.which', return_value='/usr/local/bin/claude'):
            with patch('subprocess.run', return_value=fail_result):
                with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test_key'}):
                    with patch.dict('sys.modules', {'anthropic': mock_anthropic}):
                        result = tracker.call_ai("test prompt")
        assert result == "SDK response"

    def test_no_cli_no_api_key_returns_none(self):
        env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        with patch('shutil.which', return_value=None):
            with patch.dict(os.environ, env, clear=True):
                result = tracker.call_ai("test prompt")
        assert result is None

    def test_claude_cli_timeout_falls_back(self):
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="SDK fallback")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        import subprocess as subprocess_mod
        with patch('shutil.which', return_value='/usr/local/bin/claude'):
            with patch('subprocess.run', side_effect=subprocess_mod.TimeoutExpired('claude', 180)):
                with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'key'}):
                    with patch.dict('sys.modules', {'anthropic': mock_anthropic}):
                        result = tracker.call_ai("test prompt")
        assert result == "SDK fallback"

    def test_sdk_import_error_returns_none(self, capsys):
        env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        env['ANTHROPIC_API_KEY'] = 'key'
        with patch('shutil.which', return_value=None):
            with patch.dict(os.environ, env, clear=True):
                # Simulate anthropic not installed by raising ImportError during import
                import builtins
                real_import = builtins.__import__
                def mock_import(name, *args, **kwargs):
                    if name == 'anthropic':
                        raise ImportError("No module named 'anthropic'")
                    return real_import(name, *args, **kwargs)
                with patch('builtins.__import__', side_effect=mock_import):
                    result = tracker.call_ai("prompt")
        assert result is None
        assert "anthropic" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# write_podcast_output
# ---------------------------------------------------------------------------

class TestWritePodcastOutput:
    COMMITS = [
        ("cxl: fix thing", "http://a"),
        ("cxl: add support for X", "http://b"),
    ]

    def _mock_ai(self, text="[INTRO] Welcome.\n[OUTRO] Goodbye."):
        return patch.object(tracker, 'call_ai', return_value=text)

    def test_writes_file_with_header(self, tmp_path):
        out = str(tmp_path / "podcast.md")
        cats = tracker.categorize_commits(self.COMMITS)
        with self._mock_ai():
            tracker.write_podcast_output(self.COMMITS, cats, out, "v6.13", "v6.14",
                                         "Test Author", "claude-sonnet-4-6")
        content = open(out).read()
        assert "Podcast Script" in content
        assert "v6.14" in content

    def test_ai_content_in_output(self, tmp_path):
        out = str(tmp_path / "podcast.md")
        cats = tracker.categorize_commits(self.COMMITS)
        with self._mock_ai("[INTRO] Hello.\n[OUTRO] Bye."):
            tracker.write_podcast_output(self.COMMITS, cats, out, "v6.13", "v6.14",
                                         "Author", "claude-sonnet-4-6")
        content = open(out).read()
        assert "[INTRO] Hello." in content

    def test_ai_failure_exits(self, tmp_path):
        out = str(tmp_path / "podcast.md")
        cats = tracker.categorize_commits(self.COMMITS)
        with patch.object(tracker, 'call_ai', return_value=None):
            with pytest.raises(SystemExit) as exc:
                tracker.write_podcast_output(self.COMMITS, cats, out, "v6.13", "v6.14",
                                             "Author", "claude-sonnet-4-6")
        assert exc.value.code == 1

    def test_author_in_header(self, tmp_path):
        out = str(tmp_path / "podcast.md")
        cats = tracker.categorize_commits(self.COMMITS)
        with self._mock_ai():
            tracker.write_podcast_output(self.COMMITS, cats, out, "v6.13", "v6.14",
                                         "Jane Doe", "claude-sonnet-4-6")
        content = open(out).read()
        assert "Jane Doe" in content


# ---------------------------------------------------------------------------
# write_video_short_output
# ---------------------------------------------------------------------------

class TestWriteVideoShortOutput:
    COMMITS = [
        ("cxl: fix thing", "http://a"),
        ("cxl: add support for X", "http://b"),
    ]

    def _mock_ai(self, text="[Show title] Welcome to Linux v6.14 CXL changes."):
        return patch.object(tracker, 'call_ai', return_value=text)

    def test_writes_file_with_header(self, tmp_path):
        out = str(tmp_path / "short.md")
        cats = tracker.categorize_commits(self.COMMITS)
        with self._mock_ai():
            tracker.write_video_short_output(self.COMMITS, cats, out, "v6.13", "v6.14",
                                             "claude-sonnet-4-6")
        content = open(out).read()
        assert "YouTube Short" in content
        assert "v6.14" in content

    def test_ai_content_in_output(self, tmp_path):
        out = str(tmp_path / "short.md")
        cats = tracker.categorize_commits(self.COMMITS)
        with self._mock_ai("[Show commit] Big fix landed."):
            tracker.write_video_short_output(self.COMMITS, cats, out, "v6.13", "v6.14",
                                             "claude-sonnet-4-6")
        content = open(out).read()
        assert "[Show commit]" in content

    def test_ai_failure_exits(self, tmp_path):
        out = str(tmp_path / "short.md")
        cats = tracker.categorize_commits(self.COMMITS)
        with patch.object(tracker, 'call_ai', return_value=None):
            with pytest.raises(SystemExit) as exc:
                tracker.write_video_short_output(self.COMMITS, cats, out, "v6.13", "v6.14",
                                                  "claude-sonnet-4-6")
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# write_explainers_output
# ---------------------------------------------------------------------------

class TestWriteExplainersOutput:
    COMMITS = [
        ("cxl: add support for CXL 3.0 devices", "http://a"),
        ("cxl: implement dynamic capacity regions", "http://b"),
    ]

    FEATURES_JSON = json.dumps([
        {
            "name": "CXL 3.0 support",
            "description": "Support for CXL 3.0 spec devices.",
            "why_interesting": "Enables next-gen memory.",
            "relevant_commits": ["cxl: add support for CXL 3.0 devices"],
        }
    ])

    OUTLINE = "## Suggested Title\nCXL 3.0 Explained\n## Hook\nHere is the hook.\n"

    def test_creates_output_directory(self, tmp_path):
        out_dir = str(tmp_path / "explainers")
        cats = tracker.categorize_commits(self.COMMITS)
        with patch.object(tracker, 'call_ai', side_effect=[self.FEATURES_JSON, self.OUTLINE]):
            tracker.write_explainers_output(self.COMMITS, cats, out_dir, "v6.13", "v6.14",
                                            "claude-sonnet-4-6")
        assert os.path.isdir(out_dir)

    def test_creates_one_file_per_feature(self, tmp_path):
        out_dir = str(tmp_path / "explainers")
        cats = tracker.categorize_commits(self.COMMITS)
        with patch.object(tracker, 'call_ai', side_effect=[self.FEATURES_JSON, self.OUTLINE]):
            tracker.write_explainers_output(self.COMMITS, cats, out_dir, "v6.13", "v6.14",
                                            "claude-sonnet-4-6")
        files = list(os.listdir(out_dir))
        assert len(files) == 1
        assert files[0].endswith(".md")

    def test_outline_content_written(self, tmp_path):
        out_dir = str(tmp_path / "explainers")
        cats = tracker.categorize_commits(self.COMMITS)
        with patch.object(tracker, 'call_ai', side_effect=[self.FEATURES_JSON, self.OUTLINE]):
            tracker.write_explainers_output(self.COMMITS, cats, out_dir, "v6.13", "v6.14",
                                            "claude-sonnet-4-6")
        md_file = [f for f in os.listdir(out_dir) if f.endswith(".md")][0]
        content = open(os.path.join(out_dir, md_file)).read()
        assert "CXL 3.0 Explained" in content

    def test_invalid_json_exits(self, tmp_path):
        out_dir = str(tmp_path / "explainers")
        cats = tracker.categorize_commits(self.COMMITS)
        with patch.object(tracker, 'call_ai', return_value="not json at all"):
            with pytest.raises(SystemExit) as exc:
                tracker.write_explainers_output(self.COMMITS, cats, out_dir, "v6.13", "v6.14",
                                                "claude-sonnet-4-6")
        assert exc.value.code == 1

    def test_ai_failure_exits(self, tmp_path):
        out_dir = str(tmp_path / "explainers")
        cats = tracker.categorize_commits(self.COMMITS)
        with patch.object(tracker, 'call_ai', return_value=None):
            with pytest.raises(SystemExit) as exc:
                tracker.write_explainers_output(self.COMMITS, cats, out_dir, "v6.13", "v6.14",
                                                "claude-sonnet-4-6")
        assert exc.value.code == 1

    def test_json_with_markdown_fence_parsed(self, tmp_path):
        out_dir = str(tmp_path / "explainers")
        cats = tracker.categorize_commits(self.COMMITS)
        fenced = f"```json\n{self.FEATURES_JSON}\n```"
        with patch.object(tracker, 'call_ai', side_effect=[fenced, self.OUTLINE]):
            tracker.write_explainers_output(self.COMMITS, cats, out_dir, "v6.13", "v6.14",
                                            "claude-sonnet-4-6")
        assert os.path.isdir(out_dir)


# ---------------------------------------------------------------------------
# main() — AI-gated format tests
# ---------------------------------------------------------------------------

class TestMainAiGating:
    def _tag_responses(self):
        return [
            make_response(SAMPLE_TAGS_RAW),
            make_response(TAG_DATE_RESPONSE),
            make_response(COMMITS_PATH1),
            make_response([]),
        ]

    def test_podcast_without_ai_flag_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='podcast', ai=False
            ))
        assert exc.value.code == 1
        assert "podcast" in capsys.readouterr().err

    def test_video_short_without_ai_flag_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='video-short', ai=False
            ))
        assert exc.value.code == 1

    def test_explainers_without_ai_flag_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='explainers', ai=False
            ))
        assert exc.value.code == 1

    def _tag_responses_hugo(self):
        """Like _tag_responses() but includes the extra resolve_tag_date(to_tag) call hugo needs."""
        return self._tag_responses() + [make_response(TAG_DATE_RESPONSE)]

    def test_hugo_without_ai_still_works(self, tmp_path):
        out = str(tmp_path / "post.md")
        with patch('requests.get', side_effect=self._tag_responses_hugo()):
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='hugo', output=out, ai=False
            ))
        content = open(out).read()
        assert content.startswith("---\n")

    def test_hugo_with_ai_calls_generate_function(self, tmp_path):
        out = str(tmp_path / "post.md")
        with patch('requests.get', side_effect=self._tag_responses_hugo()):
            with patch.object(tracker, '_generate_hugo_ai_content', return_value="AI text") as mock_gen:
                tracker.main(make_args(
                    start_version='v6.13', end_version='v6.14',
                    format='hugo', output=out, ai=True
                ))
        mock_gen.assert_called_once()
        content = open(out).read()
        assert "AI text" in content


# ---------------------------------------------------------------------------
# Import guard — ensure requests is available for patching
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    pytest.skip("requests library not installed", allow_module_level=True)
