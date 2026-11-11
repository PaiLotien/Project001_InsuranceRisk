"""测试运行时配置与实际用户设置隔离。"""
import pytest

@pytest.fixture(autouse=True)
def isolated_threshold_store(tmp_path, monkeypatch):
    monkeypatch.setattr('src.config.threshold_store.DB_PATH', tmp_path/'irds.db')
