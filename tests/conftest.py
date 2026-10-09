import pytest


def pytest_addoption(parser):
    parser.addoption("--docker", action="store_true", help="Run opt-in Docker integration tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--docker"):
        for item in items:
            if "docker" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Docker tests require --docker"))
