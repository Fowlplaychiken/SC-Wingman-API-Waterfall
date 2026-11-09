"""Build a deterministic Wingman skill ZIP without importing Wingman or Star Citizen."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build(out: Path) -> Path:
    out = out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError("Output directory must be outside the source checkout")
    source = ROOT / "provider_fallback_skill"
    files = {
        "__init__.py": b"",
        "main.py": (
            b'"""Wingman loader entry point."""\n'
            b"from provider_fallback_skill.skill import ProviderFallback\n"
        ),
        "default_config.yaml": (source / "default_config.yaml").read_bytes(),
        "README.md": (ROOT / "README.md").read_bytes(),
        "LICENSE": (ROOT / "LICENSE").read_bytes(),
        "NOTICE.md": (source / "NOTICE.md").read_bytes(),
    }
    for source_name, package_name in (
        ("main.py", "skill.py"),
        ("__init__.py", "__init__.py"),
        ("routing.py", "routing.py"),
        ("server.py", "server.py"),
    ):
        files["dependencies/provider_fallback_skill/" + package_name] = (
            source / source_name
        ).read_bytes()
    manifest = {
        "schema_version": 1,
        "name": "ProviderFallback",
        "version": "0.1.0",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    out.mkdir(parents=True, exist_ok=False)
    archive = out / "provider_fallback-0.1.0.zip"
    with archive.open("xb") as stream, zipfile.ZipFile(stream, "w") as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo("provider_fallback/" + name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system, info.external_attr = 3, 0o100644 << 16
            bundle.writestr(info, data)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or any(
            bundle.read("provider_fallback/" + name) != data for name, data in files.items()
        ):
            raise ValueError("Package verification failed")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    result = build(parser.parse_args().out.resolve())
    print(
        json.dumps(
            {"archive": str(result), "sha256": hashlib.sha256(result.read_bytes()).hexdigest()}
        )
    )
