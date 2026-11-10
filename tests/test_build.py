import re
from pathlib import Path

from build import PUBLIC_RAW_BASE, _public_readme


def test_bundled_readme_images_use_single_raw_github_url():
    readme = _public_readme((Path(__file__).resolve().parents[1] / "README.md").read_bytes())
    images = re.findall(rb"!\[[^\]]*\]\(([^)\s]+)\)", readme)

    assert images
    for url in images:
        assert url.startswith(PUBLIC_RAW_BASE.encode())
        assert url.count(b"https://") == 1
