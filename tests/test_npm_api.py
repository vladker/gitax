"""
Unit tests for NPMAPI class.

Tests cover:
- fetch_top_packages() with mock npm registry responses
- get_package_info() with mock package metadata
- download_package() with mock file downloads
- Caching behavior (no duplicate API calls)
- Rate limit backoff with _request_with_backoff
- Error handling (invalid packages, timeouts, network errors)
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from npm_api import (
    NPMAPI, NPMError, NetworkError, RateLimitError,
    _get_package_info_url, _get_output_dir, _file_size_mb,
)


class TestNPMAPIInit:
    """Test NPMAPI initialization"""

    def test_init(self):
        """Test class initializes without errors"""
        api = NPMAPI()
        assert api._registry_url == "https://registry.npmjs.org"
        assert api.logger is not None
        assert api._cache == {}

    def test_logger_property(self):
        """Test logger property returns correct logger"""
        api = NPMAPI()
        assert api.logger.name == "gitax"

    def test_default_timeout(self):
        """Test DEFAULT_TIMEOUT is set"""
        assert NPMAPI.DEFAULT_TIMEOUT == 60


class TestRequestWithBackoff:
    """Test _request_with_backoff rate limiting and retry logic"""

    def test_successful_request(self):
        """Test successful request returns response"""
        api = NPMAPI()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(api.session, "get", return_value=mock_response) as mock_get:
            result = api._request_with_backoff("https://example.com/api")
            assert result == mock_response
            mock_get.assert_called_once()

    def test_429_rate_limit_backoff(self):
        """Test 429 rate limit triggers backoff and retry"""
        api = NPMAPI()
        mock_limited = MagicMock()
        mock_limited.status_code = 429
        mock_limited.headers = {"Retry-After": "1"}

        mock_ok = MagicMock()
        mock_ok.status_code = 200

        with patch.object(api.session, "get", side_effect=[mock_limited, mock_ok]):
            with patch("time.sleep"):
                result = api._request_with_backoff("https://example.com/api")
                assert result == mock_ok

    def test_500_server_error_retry(self):
        """Test 5xx server error triggers retry"""
        api = NPMAPI()
        mock_error = MagicMock()
        mock_error.status_code = 502

        mock_ok = MagicMock()
        mock_ok.status_code = 200

        with patch.object(api.session, "get", side_effect=[mock_error, mock_ok]):
            with patch("time.sleep"):
                result = api._request_with_backoff("https://example.com/api")
                assert result == mock_ok

    def test_max_retries_exceeded(self):
        """Test max retries exceeded raises RateLimitError"""
        api = NPMAPI()
        mock_limited = MagicMock()
        mock_limited.status_code = 429
        mock_limited.headers = {"Retry-After": "1"}

        with patch.object(api.session, "get", return_value=mock_limited):
            with patch("time.sleep"):
                with pytest.raises(RateLimitError):
                    api._request_with_backoff("https://example.com/api")


class TestFetchTopPackages:
    """Test fetch_top_packages method"""

    def test_fetch_top_packages_success(self):
        """Test successful fetch of top packages"""
        api = NPMAPI()

        def mock_get_info(name):
            return {
                "name": name,
                "latest_version": "4.21.0",
                "description": f"Description for {name}",
            }

        with patch.object(api, "get_package_info", side_effect=mock_get_info):
            with patch.object(api, "_get_download_count", return_value=12345678):
                result = api.fetch_top_packages(2)

            assert len(result) == 2
            assert result[0]["name"] == "express"
            assert result[0]["latest_version"] == "4.21.0"
            assert result[0]["downloads_last_365_days"] == 12345678
            assert result[1]["name"] == "lodash"

    def test_fetch_top_packages_skips_failures(self):
        """Test that fetch_top_packages skips packages that fail and continues"""
        api = NPMAPI()

        call_count = {"val": 0}

        def mock_get_info(name):
            call_count["val"] += 1
            if call_count["val"] <= 2:
                raise Exception("API error")
            return {
                "name": name,
                "latest_version": "1.0.0",
                "description": "Works",
            }

        with patch.object(api, "get_package_info", side_effect=mock_get_info):
            with patch.object(api, "_get_download_count", return_value=100):
                result = api.fetch_top_packages(5)

            # First 2 packages fail, remaining 3 succeed
            assert len(result) == 3
            for pkg in result:
                assert pkg["downloads_last_365_days"] == 100

    def test_fetch_top_packages_network_error(self):
        """Test handling when all packages fail due to network errors"""
        api = NPMAPI()

        with patch.object(api, "get_package_info", side_effect=Exception("Network error")):
            result = api.fetch_top_packages(5)

            # All packages skipped, returns empty list (no crash)
            assert result == []


class TestGetPackageInfo:
    """Test get_package_info method"""

    def test_get_package_info_success(self):
        """Test successful package info fetch"""
        mock_response = {
            "name": "express",
            "description": "Fast, unopinionated, minimalist web framework",
            "dist-tags": {"latest": "4.21.0"},
            "versions": {
                "4.21.0": {
                    "dist": {
                        "shasum": "abc123",
                        "tarball": "https://registry.npmjs.org/express/-/express-4.21.0.tgz",
                        "integrity": "sha512-...",
                    }
                }
            },
        }

        api = NPMAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()
            mock_get.return_value.status_code = 200

            result = api.get_package_info("express")

            assert result["latest_version"] == "4.21.0"
            assert result["description"] == "Fast, unopinionated, minimalist web framework"

    def test_get_package_info_caches_result(self):
        """Test that repeated calls use the cache and skip HTTP requests"""
        mock_response = {
            "name": "lodash",
            "description": "Lodash modular utilities",
            "dist-tags": {"latest": "4.17.21"},
            "versions": {
                "4.17.21": {
                    "dist": {
                        "shasum": "def456",
                        "tarball": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",
                    }
                }
            },
        }

        api = NPMAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()
            mock_get.return_value.status_code = 200

            # First call — hits the network
            result1 = api.get_package_info("lodash")
            assert mock_get.call_count == 1

            # Second call — uses cache, no new HTTP request
            result2 = api.get_package_info("lodash")
            assert mock_get.call_count == 1  # still 1
            assert result1["latest_version"] == result2["latest_version"]

    def test_get_package_info_invalid_package(self):
        """Test handling of invalid package name"""
        api = NPMAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = Exception(
                "404 Not Found"
            )

            with pytest.raises(Exception):
                api.get_package_info("nonexistent-package-xyz123")

    def test_url_generation(self):
        """Test URL generation for package info"""
        url = _get_package_info_url("express")
        assert url == "https://registry.npmjs.org/express"

    def test_clear_cache(self):
        """Test cache clearing"""
        api = NPMAPI()
        api._cache["test"] = {"data": "value"}
        api.clear_cache()
        assert "test" not in api._cache


class TestDownloadPackage:
    """Test download_package method"""

    def test_download_package_success(self, tmp_path):
        """Test successful package download"""
        mock_pkg_info = {
            "latest_version": "4.21.0",
            "description": "Express",
            "dist": {
                "tarball": "https://registry.npmjs.org/express/-/express-4.21.0.tgz",
            },
        }

        api = NPMAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            with patch.object(api.session, "get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.content = b"fake tarball content"

                with patch("npm_api.os.path.exists", return_value=False):
                    with patch("npm_api.os.makedirs"):
                        with patch("npm_api.os.path.getsize", return_value=1024 * 1024):
                            with patch("builtins.open", MagicMock()):
                                result = api.download_package("express")

                                # Should return file path
                                assert isinstance(result, list)
                                assert len(result) == 1

    def test_download_package_no_files(self):
        """Test handling of package with no dist files"""
        mock_pkg_info = {
            "latest_version": "1.0.0",
            "description": "Empty pkg",
            "dist": {},
        }

        api = NPMAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            with pytest.raises(ValueError, match="No tarball"):
                api.download_package("empty-package")

    def test_download_package_uses_cache(self):
        """Test that download_package reuses cached data"""
        mock_pkg_info = {
            "latest_version": "4.0.0",
            "description": "Cached",
            "dist": {
                "tarball": "https://registry.npmjs.org/pkg/-/pkg-4.0.0.tgz",
            },
        }

        api = NPMAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            with patch.object(api.session, "get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.content = b"fake"

                with patch("npm_api.os.path.exists", return_value=True):
                    with patch("npm_api.os.makedirs"):
                        with patch("npm_api.os.path.getsize", return_value=512):
                            with patch("builtins.open", MagicMock()):
                                api.download_package("cached-pkg")
                                assert mock_get.call_count == 0


class TestHelperFunctions:
    """Test helper functions"""

    def test_get_output_dir(self):
        """Test output directory generation"""
        result = _get_output_dir("express")
        assert "express" in result
        assert result.startswith("./temp_npm") or result.startswith("temp_npm")

    def test_file_size_mb(self, tmp_path):
        """Test file size calculation"""
        from npm_api import _file_size_mb

        test_file = tmp_path / "test.tgz"
        test_file.write_bytes(b"x" * 1024 * 1024)  # 1MB

        size = _file_size_mb(str(test_file))
        assert abs(size - 1.0) < 0.01


class TestExceptions:
    """Test custom exception hierarchy"""

    def test_exception_hierarchy(self):
        """Test that custom exceptions inherit from NPMError"""
        assert issubclass(NetworkError, NPMError)
        assert issubclass(RateLimitError, NPMError)
        assert issubclass(NPMError, Exception)
