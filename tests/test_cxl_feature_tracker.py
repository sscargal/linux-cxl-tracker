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

    def test_version_without_v_in_section_heading(self, tmp_path):
        content = self._write(tmp_path)
        assert "6.14 Kernel" in content


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
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
            tracker.main(make_args(
                start_version='v6.13', end_version='v6.14',
                format='hugo', output=out
            ))
        content = open(out).read()
        assert content.startswith("---\n")
        assert "draft: false" in content

    def test_hugo_default_filename_when_no_output(self, tmp_path, capsys):
        tag_resp = make_response(SAMPLE_TAGS_RAW)
        tag_date_resp = make_response(TAG_DATE_RESPONSE)
        commits_resp = make_response(COMMITS_PATH1)
        commits_resp2 = make_response([])
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
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
        with patch('requests.get', side_effect=[tag_resp, tag_date_resp, commits_resp, commits_resp2]):
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


# ---------------------------------------------------------------------------
# Import guard — ensure requests is available for patching
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    pytest.skip("requests library not installed", allow_module_level=True)
