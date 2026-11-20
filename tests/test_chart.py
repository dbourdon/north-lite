"""Chart checks that need more than one render or the helm CLI itself.

helm-unittest (charts/north-lite/tests) covers single renders; these cover
"does X change when Y changes", schema validation and packaging.
"""

import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "charts" / "north-lite"
PROFILES = sorted(p.stem.removeprefix("values-flags-") for p in CHART.glob("values-flags-*.yaml"))

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def helm(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["helm", *args], capture_output=True, text=True, check=False)


def render(*sets: str, files: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    args = ["template", "nl", str(CHART), "--set", "config.backend=fake"]
    for f in files:
        args += ["-f", str(CHART / f)]
    for s in sets:
        args += ["--set", s]
    out = helm(*args)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def pod_annotations(docs: list[dict[str, Any]]) -> dict[str, str]:
    [deploy] = [d for d in docs if d["kind"] == "Deployment"]
    annotations: dict[str, str] = deploy["spec"]["template"]["metadata"]["annotations"]
    return annotations


def test_config_checksum_changes_with_config() -> None:
    before = pod_annotations(render())["checksum/config"]
    after = pod_annotations(render("features.rerank=false"))["checksum/config"]
    assert before != after  # pod template changes -> rolling update


def test_config_checksum_stable_when_nothing_changes() -> None:
    # Same input, same hash: an unrelated `helm upgrade` must not restart pods.
    assert (
        pod_annotations(render())["checksum/config"] == pod_annotations(render())["checksum/config"]
    )


def test_secret_checksum_changes_with_key_value() -> None:
    a = pod_annotations(render("apiKey.value=one"))["checksum/secret"]
    b = pod_annotations(render("apiKey.value=two"))["checksum/secret"]
    assert a != b


@pytest.mark.parametrize(
    ("bad_set", "complaint"),
    [
        ("replicaCount=abc", "replicaCount"),
        ("features.rerak=true", "rerak"),  # typo in a flag name
        ("config.backend=openai", "backend"),
        ("service.port=70000", "port"),
    ],
    ids=["replicas-not-int", "flag-typo", "unknown-backend", "port-range"],
)
def test_schema_rejects_bad_values(bad_set: str, complaint: str) -> None:
    out = helm("template", "nl", str(CHART), "--set", "config.backend=fake", "--set", bad_set)
    assert out.returncode != 0
    assert "schema" in out.stderr.lower()
    assert complaint in out.stderr


@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_lints_with_ci_values(profile: str) -> None:
    out = helm(
        "lint", str(CHART), "--strict",
        "-f", str(CHART / "values-ci.yaml"),
        "-f", str(CHART / f"values-flags-{profile}.yaml"),
    )  # fmt: skip
    assert out.returncode == 0, out.stdout + out.stderr


def test_app_version_matches_pyproject() -> None:
    # One version for the release: the tag, the package and the chart's appVersion.
    # The release workflow checks the tag; this keeps the other two from drifting.
    import tomllib

    root = CHART.parents[1]
    pkg = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["appVersion"] == pkg


def test_release_pin_overrides_image_for_gitops() -> None:
    # Argo's value-file order: values-ci, profile, deploy/release.yaml (last wins).
    root = CHART.parents[1]
    out = helm(
        "template", "nl", str(CHART),
        "-f", str(CHART / "values-ci.yaml"),
        "-f", str(CHART / "values-flags-full.yaml"),
        "-f", str(root / "deploy" / "release.yaml"),
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    pin = yaml.safe_load((root / "deploy" / "release.yaml").read_text())["image"]
    [deploy] = [d for d in yaml.safe_load_all(out.stdout) if d and d["kind"] == "Deployment"]
    image = deploy["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == f"{pin['repository']}:{pin['tag']}"


def test_default_image_is_the_release_for_app_version() -> None:
    [deploy] = [d for d in render() if d["kind"] == "Deployment"]
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    image = deploy["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == f"ghcr.io/dbourdon/north-lite:{chart['appVersion']}"


def test_profiles_exist() -> None:
    assert {"minimal", "full", "no-rerank", "airgapped"} <= set(PROFILES)


def test_package_excludes_profiles_and_unit_tests(tmp_path: Path) -> None:
    out = helm("package", str(CHART), "-d", str(tmp_path))
    assert out.returncode == 0, out.stderr
    [pkg] = tmp_path.glob("*.tgz")
    names = tarfile.open(pkg).getnames()
    assert "north-lite/templates/tests/test-flags.yaml" in names  # helm test hook kept
    assert not any(n.startswith("north-lite/tests/") for n in names)  # unit tests dropped
    assert not any("values-flags-" in n or "values-ci" in n for n in names)
