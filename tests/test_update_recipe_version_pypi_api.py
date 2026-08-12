"""Tests for the PyPI simple API path of the version updater.

The URL that the simple API reports for a file is content-addressed: its path
segments are a hash of that one file. Writing it into a recipe means no later
version bump can render a working URL, so these tests pin down that we prefer
the stable `/packages/source/` form and that we take the hash from the index
rather than downloading the file.
"""

import pytest

from conda_forge_tick.update_recipe.version import _try_pypi_api

NAME = "lbaplocal"
VERSION = "0.12.1"
SHA256 = "ed8d09bbf3bce158edac812b437cc511ecc8b9e2ab35f2058a4066ea933dba37"
BLOB_DIR = "https://files.pythonhosted.org/packages/27/00/d89a1d6db222b236071d58fb736f2b37d75e7a4d3dec2a432cedbad5042b"
OLD_BLOB_DIR = "https://files.pythonhosted.org/packages/38/22/a931a73a747a9a86ac7c97775056e235457c2131664ba2cfab0c8925e6fb"
CANONICAL_TMPL = (
    "https://pypi.org/packages/source/l/lbaplocal/{{ name }}-{{ version }}.tar.gz"
)

CONTEXT = {"name": NAME, "version": VERSION}
CMETA = {"package": {"name": NAME, "version": VERSION}}


def _index(files=None):
    if files is None:
        files = [
            {
                "filename": f"{NAME}-{VERSION}.tar.gz",
                "hashes": {"sha256": SHA256},
                "url": f"{BLOB_DIR}/{NAME}-{VERSION}.tar.gz",
                "yanked": False,
            }
        ]
    return {"files": files, "meta": {"api-version": "1.4"}, "name": NAME}


@pytest.fixture
def no_downloads(monkeypatch):
    """Fail loudly if the updater tries to download anything."""
    calls = []

    def _boom(url, hash_type):
        calls.append(url)
        return None

    monkeypatch.setattr(
        "conda_forge_tick.update_recipe.version._try_url_and_hash_it", _boom
    )
    return calls


def test_pypi_api_keeps_canonical_url_without_downloading(requests_mock, no_downloads):
    """A recipe already using the canonical URL keeps its own template verbatim.

    The hash comes from the index, so a release that the CDN has not caught up
    with yet does not fail the update.
    """
    requests_mock.get(f"https://pypi.org/simple/{NAME}/", json=_index())

    url_tmpl = (
        "https://pypi.org/packages/source/{{ name[0] }}/{{ name }}/"
        "{{ name }}-{{ version }}.tar.gz"
    )
    new_url_tmpl, new_hash = _try_pypi_api(url_tmpl, CONTEXT, "sha256", CMETA)

    assert new_url_tmpl == url_tmpl
    assert new_hash == SHA256
    assert no_downloads == []


def test_pypi_api_does_not_write_content_addressed_url(requests_mock, no_downloads):
    """A stale content-addressed URL heals to the canonical form.

    The old blob directory is only correct for the version it was written for,
    so rewriting it to the new blob directory just moves the breakage to the
    next bump.
    """
    requests_mock.get(f"https://pypi.org/simple/{NAME}/", json=_index())

    url_tmpl = f"{OLD_BLOB_DIR}/{{{{ name }}}}-{{{{ version }}}}.tar.gz"
    new_url_tmpl, new_hash = _try_pypi_api(url_tmpl, CONTEXT, "sha256", CMETA)

    assert new_url_tmpl == CANONICAL_TMPL
    assert new_hash == SHA256
    assert "files.pythonhosted.org" not in new_url_tmpl
    # the dead blob URL is the one thing we were willing to try downloading
    assert no_downloads == [f"{OLD_BLOB_DIR}/{NAME}-{VERSION}.tar.gz"]


def test_pypi_api_falls_back_to_download_when_index_has_no_hash(
    requests_mock, monkeypatch
):
    """md5 recipes still work: the index only publishes sha256."""
    requests_mock.get(f"https://pypi.org/simple/{NAME}/", json=_index())

    hashed = []

    def _hash_it(url, hash_type):
        hashed.append(url)
        return "d3b07384d113edec49eaa6238ad5ff00"

    monkeypatch.setattr(
        "conda_forge_tick.update_recipe.version._try_url_and_hash_it", _hash_it
    )

    url_tmpl = (
        "https://pypi.org/packages/source/{{ name[0] }}/{{ name }}/"
        "{{ name }}-{{ version }}.tar.gz"
    )
    new_url_tmpl, new_hash = _try_pypi_api(url_tmpl, CONTEXT, "md5", CMETA)

    assert new_url_tmpl == url_tmpl
    assert new_hash == "d3b07384d113edec49eaa6238ad5ff00"
    assert hashed == [f"https://pypi.org/packages/source/l/{NAME}/{NAME}-{VERSION}.tar.gz"]


def test_pypi_api_returns_nothing_when_version_missing(requests_mock, no_downloads):
    """A stale index must not be papered over with a guessed URL."""
    requests_mock.get(
        f"https://pypi.org/simple/{NAME}/",
        json=_index(
            files=[
                {
                    "filename": f"{NAME}-0.12.0.tar.gz",
                    "hashes": {"sha256": "0" * 64},
                    "url": f"{OLD_BLOB_DIR}/{NAME}-0.12.0.tar.gz",
                    "yanked": False,
                }
            ]
        ),
    )

    url_tmpl = (
        "https://pypi.org/packages/source/{{ name[0] }}/{{ name }}/"
        "{{ name }}-{{ version }}.tar.gz"
    )
    assert _try_pypi_api(url_tmpl, CONTEXT, "sha256", CMETA) == (None, None)
    assert no_downloads == []
