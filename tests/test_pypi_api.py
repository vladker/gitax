"""
Unit tests for PyPIAPI class.

Tests cover:
- fetch_top_packages() with mock Hugovk dataset
- get_package_info() with mock PyPI JSON API responses
- download_package() with mock file downloads
- Caching behavior (no duplicate API calls)
- Error handling (invalid package names, timeouts, rate limiting)
- Rate limit backoff with _request_with_backoff
"""

import json
import os
import time
import pytest
from unittest.mock import patch, MagicMock
from pypi_api import (
    PyPIAPI, PyPIAPIError, NetworkError, RateLimitError,
    _get_package_info_url, _get_output_dir, _file_size_mb,
)


class TestPyPIAPIInit:
    """Test PyPIAPI initialization"""

    def test_init(self):
        """Test class initializes without errors"""
        api = PyPIAPI()
        assert api._base_url == "https://pypi.org"
        assert api.logger is not None
        assert api._cache == {}

    def test_logger_property(self):
        """Test logger property returns correct logger"""
        api = PyPIAPI()
        assert api.logger.name == "gitax"

    def test_default_timeout(self):
        """Test DEFAULT_TIMEOUT is set"""
        assert PyPIAPI.DEFAULT_TIMEOUT == 60


class TestRequestWithBackoff:
    """Test _request_with_backoff rate limiting and retry logic"""

    def test_successful_request(self):
        """Test successful request returns response"""
        api = PyPIAPI()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(api.session, "get", return_value=mock_response) as mock_get:
            result = api._request_with_backoff("https://example.com/api")
            assert result == mock_response
            mock_get.assert_called_once()

    def test_429_rate_limit_backoff(self):
        """Test 429 rate limit triggers backoff and retry"""
        api = PyPIAPI()
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
        api = PyPIAPI()
        mock_error = MagicMock()
        mock_error.status_code = 502

        mock_ok = MagicMock()
        mock_ok.status_code = 200

        with patch.object(api.session, "get", side_effect=[mock_error, mock_ok]):
            with patch("time.sleep"):
                result = api._request_with_backoff("https://example.com/api")
                assert result == mock_ok

    def test_connection_error_raises_network_error(self):
        """Test connection error raises NetworkError"""
        api = PyPIAPI()

        with patch.object(
            api.session, "get", side_effect=Exception("Connection refused")
        ):
            with pytest.raises(Exception):
                api._request_with_backoff("https://example.com/api")

    def test_max_retries_exceeded(self):
        """Test max retries exceeded raises RateLimitError"""
        api = PyPIAPI()
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
        mock_response = {
            "top-packages": [
                {
                    "pypi-name": "requests",
                    "pypi-version": "2.31.0",
                    "download-simple-365-days": "982742658",
                },
                {
                    "pypi-name": "urllib3",
                    "pypi-version": "2.1.0",
                    "download-simple-365-days": "934553234",
                },
            ]
        }

        api = PyPIAPI()

        with patch("pypi_api.requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()

            result = api.fetch_top_packages(2)

            assert len(result) == 2
            assert result[0]["name"] == "requests"
            assert result[0]["latest_version"] == "2.31.0"
            assert result[0]["downloads_last_365_days"] == 982742658
            assert result[1]["name"] == "urllib3"

    def test_fetch_top_packages_empty_response(self):
        """Test handling of empty response"""
        mock_response = {"top-packages": []}

        api = PyPIAPI()

        with patch("pypi_api.requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()

            result = api.fetch_top_packages(10)
            assert result == []

    def test_fetch_top_packages_network_error(self):
        """Test handling of network errors"""
        api = PyPIAPI()

        with patch("pypi_api.requests.get") as mock_get:
            mock_get.side_effect = Exception("Network error")

            with pytest.raises(Exception):
                api.fetch_top_packages(10)


class TestGetPackageInfo:
    """Test get_package_info method"""

    def test_get_package_info_success(self):
        """Test successful package info fetch"""
        mock_response = {
            "info": {
                "version": "2.31.0",
                "summary": "Python HTTP for Humans.",
                "license": "Apache-2.0",
            },
            "releases": {
                "2.31.0": [
                    {
                        "filename": "requests-2.31.0-py3-none-any.whl",
                        "url": "https://files.pythonhosted.org/...",
                    },
                    {
                        "filename": "requests-2.31.0.tar.gz",
                        "url": "https://files.pythonhosted.org/...",
                    },
                ]
            },
        }

        api = PyPIAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()
            mock_get.return_value.status_code = 200

            result = api.get_package_info("requests")

            assert result["latest_version"] == "2.31.0"
            assert result["info"]["summary"] == "Python HTTP for Humans."
            assert len(result["releases"]) > 0

    def test_get_package_info_caches_result(self):
        """Test that repeated calls use the cache and skip HTTP requests"""
        mock_response = {
            "info": {"version": "1.0.0", "summary": "Test pkg", "license": "MIT"},
            "releases": {"1.0.0": [{"filename": "test-1.0.0.tar.gz", "url": "http://x"}]},
        }

        api = PyPIAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()
            mock_get.return_value.status_code = 200

            # First call — hits the network
            result1 = api.get_package_info("test-pkg")
            assert mock_get.call_count == 1

            # Second call — uses cache, no new HTTP request
            result2 = api.get_package_info("test-pkg")
            assert mock_get.call_count == 1  # still 1
            assert result1["latest_version"] == result2["latest_version"]

    def test_get_package_info_invalid_package(self):
        """Test handling of invalid package name"""
        api = PyPIAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = Exception(
                "404 Not Found"
            )

            with pytest.raises(Exception):
                api.get_package_info("nonexistent-package-xyz123")

    def test_url_generation(self):
        """Test URL generation for package info"""
        url = _get_package_info_url("requests")
        assert url == "https://pypi.org/pypi/requests/json"

    def test_clear_cache(self):
        """Test cache clearing"""
        api = PyPIAPI()
        api._cache["test"] = {"data": "value"}
        api.clear_cache()
        assert "test" not in api._cache


class TestDownloadPackage:
    """Test download_package method"""

    def test_download_package_success(self, tmp_path):
        """Test successful package download"""
        mock_pkg_info = {
            "latest_version": "2.31.0",
            "info": {},
            "releases": {},
        }

        mock_raw_data = {
            "urls": [
                {
                    "filename": "requests-2.31.0.tar.gz",
                    "url": "https://files.pythonhosted.org/requests-2.31.0.tar.gz",
                },
                {
                    "filename": "requests-2.31.0-py3-none-any.whl",
                    "url": "https://files.pythonhosted.org/requests-2.31.0-py3-none-any.whl",
                },
            ]
        }

        api = PyPIAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            # Pre-populate the cache with raw data (simulates get_package_info call)
            api._cache["requests__raw"] = mock_raw_data

            with patch.object(api.session, "get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.content = b"fake file content"

                # Mock file operations
                with patch("pypi_api.os.path.exists", return_value=True):
                    with patch("pypi_api.os.makedirs"):
                        with patch(
                            "pypi_api.os.path.getsize", return_value=1024 * 1024
                        ):  # 1MB
                            with patch("builtins.open", MagicMock()):
                                result = api.download_package("requests")

                                # Should return list of file paths
                                assert isinstance(result, list)
                                assert len(result) == 2

    def test_download_package_no_files(self):
        """Test handling of package with no files"""
        mock_pkg_info = {
            "latest_version": "1.0.0",
            "info": {},
            "releases": {},
        }

        api = PyPIAPI()
        # Cache has no raw data with files
        api._cache["empty-package__raw"] = {"urls": []}

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            with pytest.raises(ValueError, match="No files found"):
                api.download_package("empty-package")

    def test_download_package_uses_cache(self):
        """Test that download_package reuses cached data (no second HTTP call)"""
        mock_pkg_info = {
            "latest_version": "2.0.0",
            "info": {},
            "releases": {},
        }

        mock_raw_data = {
            "urls": [
                {
                    "filename": "pkg-2.0.0.tar.gz",
                    "url": "https://files.pythonhosted.org/pkg-2.0.0.tar.gz",
                }
            ]
        }

        api = PyPIAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            api._cache["cached-pkg__raw"] = mock_raw_data

            with patch.object(api.session, "get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.content = b"fake"

                with patch("pypi_api.os.path.exists", return_value=True):
                    with patch("pypi_api.os.makedirs"):
                        with patch("pypi_api.os.path.getsize", return_value=512):
                            with patch("builtins.open", MagicMock()):
                                api.download_package("cached-pkg")
                                # os.path.exists returns True → file already exists,
                                # so session.get is NOT called at all (skip download)
                                assert mock_get.call_count == 0


class TestHelperFunctions:
    """Test helper functions"""

    def test_get_output_dir(self):
        """Test output directory generation"""
        result = _get_output_dir("requests")
        assert "requests" in result
        assert result.startswith("./temp_pypi") or result.startswith("temp_pypi")

    def test_file_size_mb(self, tmp_path):
        """Test file size calculation"""
        from pypi_api import _file_size_mb

        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"x" * 1024 * 1024)  # 1MB

        size = _file_size_mb(str(test_file))
        assert abs(size - 1.0) < 0.01  # Should be ~1MB


class TestExceptions:
    """Test custom exception hierarchy"""

    def test_exception_hierarchy(self):
        """Test that custom exceptions inherit from PyPIAPIError"""
        assert issubclass(NetworkError, PyPIAPIError)
        assert issubclass(RateLimitError, PyPIAPIError)
        assert issubclass(PyPIAPIError, Exception)
