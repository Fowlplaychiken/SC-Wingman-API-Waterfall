"""Build a deterministic Wingman skill ZIP without importing Wingman or Star Citizen."""

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLIC_BASE = "https://github.com/Fowlplaychiken/SC-Wingman-API-Waterfall/blob/NewRepo/"
PUBLIC_RAW_BASE = "https://raw.githubusercontent.com/Fowlplaychiken/SC-Wingman-API-Waterfall/NewRepo/"


def _public_readme(data: bytes) -> bytes:
    """Make repository-only README links usable from the standalone ZIP."""
    def rewrite(match: re.Match[bytes]) -> bytes:
        destination = match.group(1)
        if destination.startswith(b"docs/diagrams/"):
            target = PUBLIC_RAW_BASE.encode() + destination
        elif destination in {
            b"benchmarks/WINGMAN-JEV-PAIRED-COMPARISON.png",
            b"benchmarks/WINGMAN-SUPPORT-24.png",
        }:
            target = PUBLIC_RAW_BASE.encode() + destination
        elif destination.startswith(b"docs/MODEL-ROLES.md"):
            target = (PUBLIC_BASE + "docs/MODEL-ROLES.md").encode() + destination[
                len(b"docs/MODEL-ROLES.md") :
            ]
        elif destination.startswith(b"benchmarks/"):
            target = (PUBLIC_BASE + "benchmarks/").encode() + destination[len(b"benchmarks/") :]
        else:
            target = destination
        return b"](" + target + b")"

    return re.sub(rb"\]\(([^)\s]+)\)", rewrite, data)


def build(out: Path) -> Path:
    out = out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError("Output directory must be outside the source checkout")
    source = ROOT / "provider_fallback_skill"
    config = (source / "default_config.yaml").read_bytes()
    match = re.search(rb"(?m)^version: ([0-9]+\.[0-9]+\.[0-9]+)\r?$", config)
    if match is None:
        raise ValueError("Explicit semantic skill version required")
    version = match.group(1).decode("ascii")
    namespace = "provider_fallback_skill_v" + version.replace(".", "_")
    files = {
        "__init__.py": b"",
        "main.py": (
            b'"""Original Wingman loader entry point."""\n'
            + f"from {namespace}.skill import ProviderFallback\n".encode()
        ),
        "default_config.yaml": config,
        "README.md": _public_readme((ROOT / "README.md").read_bytes()),
        "CONFIGURATION.md": (ROOT / "CONFIGURATION.md").read_bytes(),
        "LICENSE": (ROOT / "LICENSE").read_bytes(),
        "NOTICE.md": (source / "NOTICE.md").read_bytes(),
    }
    for source_name, package_name in (
        ("main.py", "skill.py"),
        ("__init__.py", "__init__.py"),
        ("routing.py", "routing.py"),
        ("context_budget.py", "context_budget.py"),
        ("server.py", "server.py"),
        ("native_messages.py", "native_messages.py"),
        ("jev_assist.py", "jev_assist.py"),
        ("setup_engine.py", "setup_engine.py"),
        ("setup_web.py", "setup_web.py"),
        ("setup.html", "setup.html"),
    ):
        files[f"dependencies/{namespace}/" + package_name] = (source / source_name).read_bytes()
    manifest = {
        "schema_version": 1,
        "name": "ProviderFallback",
        "version": version,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    out.mkdir(parents=True, exist_ok=False)
    archive = out / f"provider_fallback-{version}.zip"
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
