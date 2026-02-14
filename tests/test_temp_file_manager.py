"""Tests for temporary file manager."""

import json
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.utils.temp_file_manager import CachedContent, TempFileManager
from tests.testing_utils import StubLifecycleCoordinator


@contextmanager
def make_temp_file_manager(cleanup_age_hours: float = 1.0):
    """Context manager to create a TempFileManager with temporary directory."""
    with tempfile.TemporaryDirectory() as temp_base:
        manager = TempFileManager(
            base_path=temp_base,
            cleanup_age_hours=cleanup_age_hours,
            lifecycle_coordinator=StubLifecycleCoordinator(),
        )
        try:
            yield manager
        finally:
            manager.shutdown()


class TestTempFileManager:
    """Test cases for TempFileManager."""

    def test_create_temp_directory(self):
        with make_temp_file_manager(cleanup_age_hours=1.0) as manager:
            temp_base = str(manager.base_path)
            temp_dir = manager.create_temp_directory()
            assert temp_dir.exists()
            assert temp_dir.is_dir()
            assert temp_base in str(temp_dir)
            dir_name = temp_dir.name
            assert "_" in dir_name

    def test_cleanup_old_files(self):
        with make_temp_file_manager(cleanup_age_hours=0.001) as manager:
            temp_dir1 = manager.create_temp_directory()
            temp_dir2 = manager.create_temp_directory()
            (temp_dir1 / "test1.pdf").write_text("content 1")
            (temp_dir2 / "test2.pdf").write_text("content 2")

            time.sleep(4)

            temp_dir3 = manager.create_temp_directory()
            (temp_dir3 / "test3.pdf").write_text("content 3")

            cleaned_count = manager.cleanup_old_files()

            assert cleaned_count == 2
            assert not temp_dir1.exists()
            assert not temp_dir2.exists()
            assert temp_dir3.exists()

    def test_cleanup_thread_lifecycle(self):
        with make_temp_file_manager() as manager:
            manager.start_cleanup_thread()
            assert manager._cleanup_thread is not None
            assert manager._cleanup_thread.is_alive()
            manager.shutdown()
            time.sleep(0.1)
            assert not manager._cleanup_thread.is_alive()

    def test_cleanup_nonexistent_base_directory(self):
        with make_temp_file_manager() as manager:
            import shutil

            nested_path = str(manager.base_path / "ai_analysis")
            Path(nested_path).mkdir(parents=True, exist_ok=True)
            manager.base_path = Path(nested_path)
            shutil.rmtree(nested_path)
            cleaned_count = manager.cleanup_old_files()
            assert cleaned_count == 0

    def test_multiple_temp_directories_different_timestamps(self):
        with make_temp_file_manager() as manager:
            dirs = [manager.create_temp_directory() for _ in range(5)]
            dir_names = [d.name for d in dirs]
            assert len(set(dir_names)) == 5
            for temp_dir in dirs:
                assert temp_dir.exists()

    def test_base_path_creation(self):
        with tempfile.TemporaryDirectory() as parent_temp:
            base_path = str(Path(parent_temp) / "ai_analysis" / "nested")
            assert not Path(base_path).exists()
            TempFileManager(
                base_path=base_path,
                cleanup_age_hours=1.0,
                lifecycle_coordinator=StubLifecycleCoordinator(),
            )
            assert Path(base_path).exists()

    def test_url_to_path(self):
        with make_temp_file_manager() as manager:
            url1 = "https://example.com/test.pdf"
            url2 = "https://different.com/test.pdf"
            path1 = manager._url_to_path(url1)
            path2 = manager._url_to_path(url2)
            assert path1 != path2
            assert manager._url_to_path(url1) == path1
            assert len(path1) == 64

    def test_cache_and_get_cached(self):
        with make_temp_file_manager(cleanup_age_hours=1.0) as manager:
            url = "https://example.com/test.txt"
            content = b"test content for caching"
            content_type = "text/plain"
            success = manager.cache(url, content, content_type)
            assert success is True
            cached = manager.get_cached(url)
            assert cached is not None
            assert isinstance(cached, CachedContent)
            assert cached.content == content
            assert cached.content_type == content_type
            assert isinstance(cached.timestamp, datetime)

    def test_cache_and_get_different_urls(self):
        with make_temp_file_manager(cleanup_age_hours=1.0) as manager:
            manager.cache("https://example.com/file1.txt", b"content 1", "text/plain")
            manager.cache("https://example.com/file2.txt", b"content 2", "text/html")
            cached1 = manager.get_cached("https://example.com/file1.txt")
            cached2 = manager.get_cached("https://example.com/file2.txt")
            assert cached1.content == b"content 1"
            assert cached2.content == b"content 2"

    def test_get_cached_nonexistent(self):
        with make_temp_file_manager() as manager:
            assert manager.get_cached("https://example.com/nonexistent.txt") is None

    def test_get_cached_expired(self):
        with make_temp_file_manager(cleanup_age_hours=0.001) as manager:
            url = "https://example.com/expires.txt"
            manager.cache(url, b"content that will expire", "text/plain")
            assert manager.get_cached(url) is not None
            time.sleep(4)
            assert manager.get_cached(url) is None

    def test_cache_metadata_format(self):
        with make_temp_file_manager() as manager:
            url = "https://example.com/metadata-test.pdf"
            content = b"PDF content for metadata test"
            content_type = "application/pdf"
            manager.cache(url, content, content_type)

            cache_key = manager._url_to_path(url)
            metadata_file = manager.cache_path / f"{cache_key}.json"
            assert metadata_file.exists()

            with open(metadata_file) as f:
                metadata = json.load(f)

            assert metadata["url"] == url
            assert metadata["content_type"] == content_type
            assert metadata["size"] == len(content)
            datetime.fromisoformat(metadata["timestamp"])

    def test_get_cached_corrupt_metadata(self):
        with make_temp_file_manager() as manager:
            url = "https://example.com/corrupt-test.txt"
            cache_key = manager._url_to_path(url)
            content_file = manager.cache_path / f"{cache_key}.bin"
            metadata_file = manager.cache_path / f"{cache_key}.json"
            with open(content_file, "wb") as f:
                f.write(b"test content")
            with open(metadata_file, "w") as f:
                f.write("invalid json {")
            assert manager.get_cached(url) is None

    def test_get_cached_missing_content_file(self):
        with make_temp_file_manager() as manager:
            url = "https://example.com/missing-content.txt"
            cache_key = manager._url_to_path(url)
            metadata_file = manager.cache_path / f"{cache_key}.json"
            metadata = {
                "url": url,
                "content_type": "text/plain",
                "timestamp": datetime.now().isoformat(),
                "size": 100,
            }
            with open(metadata_file, "w") as f:
                json.dump(metadata, f)
            assert manager.get_cached(url) is None

    def test_cached_content_namedtuple(self):
        timestamp = datetime.now()
        cached = CachedContent(
            content=b"test content",
            content_type="text/plain",
            timestamp=timestamp,
        )
        assert cached.content == b"test content"
        assert cached.content_type == "text/plain"
        assert cached.timestamp == timestamp
