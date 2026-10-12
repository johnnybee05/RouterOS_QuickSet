"""Fetching RouterOS packages: the names MikroTik gives them, and not trusting those names.

A file that is not what it claims is the dangerous outcome here, not a failed download. An
error page saved as `wireless-7.16.1-arm.npk` would upload, the device would reboot, and
the radio would be gone — so every test about what arrives checks that the header, not the
file name, is what decides.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from factories import make_inventory, make_npk, make_radios
from mqs.apply.download import (
    DownloadFailed,
    PackageCache,
    PackageSource,
)
from mqs.apply.package_set import wanted_packages
from mqs.apply.packages import npk_file_name, npk_url
from mqs.model.inventory import PackageInfo, WirelessDriver
from mqs.model.version import RosVersion

V = RosVersion.parse
MB = 1024 * 1024


# ------------------------------------------------------------------ a server to ask


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200) -> None:
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeSite:
    """What download.mikrotik.com publishes, as a mapping of URL -> bytes."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.requests: list[tuple[str, str]] = []  # (method, url)

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append((request.get_method(), request.full_url))
        data = self.files.get(request.full_url)
        if data is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return FakeResponse(b"" if request.get_method() == "HEAD" else data)

    @property
    def gets(self) -> list[str]:
        return [url for method, url in self.requests if method == "GET"]

    @property
    def heads(self) -> list[str]:
        return [url for method, url in self.requests if method == "HEAD"]


def published(*entries: tuple[str, str, str]) -> dict[str, bytes]:
    """``entries`` are (package, version, device arch), laid out as MikroTik lays them."""
    site: dict[str, bytes] = {}
    for package, text, arch in entries:
        version = V(text)
        parts = (version.major, version.minor, version.patch)
        header = "system" if package == "routeros" and version.major >= 7 else package
        if package == "routeros" and version.major == 6:
            header = f"routeros-{arch}"
        site[npk_url(package, version, arch)] = make_npk(
            header, parts, _header_arch(arch), bundle=version.major == 6)
    return site


def _header_arch(device_arch: str) -> str:
    return {"mipsbe": "mips", "x86": "i386"}.get(device_arch, device_arch)


def cache_at(tmp_path: Path, site: FakeSite) -> PackageCache:
    return PackageCache(tmp_path / "cache", opener=site)


# ------------------------------------------------------------------ the file names


@pytest.mark.parametrize("package, version, arch, expected", [
    # v7: the version, then the architecture
    ("routeros", "7.23.7", "arm", "routeros-7.23.7-arm.npk"),
    ("wifi-qcom", "7.23.7", "arm", "wifi-qcom-7.23.7-arm.npk"),
    ("wireless", "7.16.1", "mmips", "wireless-7.16.1-mmips.npk"),
    # the architecture in the name is the DEVICE's, not the header's: a mipsbe file is
    # called -mipsbe.npk and says `mips` inside
    ("routeros", "7.23.7", "mipsbe", "routeros-7.23.7-mipsbe.npk"),
    # v6 puts them the other way round, and the main package is the bundle
    ("routeros", "6.49.21", "mmips", "routeros-mmips-6.49.21.npk"),
    ("routeros", "6.49.22", "mipsbe", "routeros-mipsbe-6.49.22.npk"),
    # x86 carries no architecture at all, except in the name of the v6 bundle
    ("routeros", "7.23.7", "x86", "routeros-7.23.7.npk"),
    ("wireless", "7.24.4", "x86", "wireless-7.24.4.npk"),
    ("routeros", "6.49.23", "x86", "routeros-x86-6.49.23.npk"),
    ("wireless", "6.49.23", "x86", "wireless-6.49.23.npk"),
    # PowerPC is `ppc` in a file name, but the v6 bundle spells it out
    ("routeros", "7.24.4", "powerpc", "routeros-7.24.4-ppc.npk"),
    ("routeros", "6.49.23", "powerpc", "routeros-powerpc-6.49.23.npk"),
    ("wireless", "6.49.23", "powerpc", "wireless-6.49.23-ppc.npk"),
    # a v6 package outside the bundle has the version first, like a v7 one
    ("wireless", "6.49.23", "mmips", "wireless-6.49.23-mmips.npk"),
    # a zero patch is not written, matching MikroTik's own 7.16
    ("routeros", "7.16", "arm", "routeros-7.16-arm.npk"),
])
def test_the_file_name_follows_mikrotiks_own(package, version, arch, expected):
    assert npk_file_name(package, V(version), arch) == expected


def test_the_main_package_is_asked_for_under_one_name():
    """It goes by three — `routeros` in a URL, `system` in a v7 header, `routeros-<arch>`
    in a v6 one — and only the URL name is this function's business."""
    assert npk_file_name("system", V("7.23.7"), "arm") == "routeros-7.23.7-arm.npk"
    assert npk_file_name("routeros-mmips", V("6.49.21"), "mmips") == \
        "routeros-mmips-6.49.21.npk"


def test_the_url_is_the_version_directory():
    assert npk_url("wireless", V("7.16.1"), "arm") == \
        "https://download.mikrotik.com/routeros/7.16.1/wireless-7.16.1-arm.npk"


# ------------------------------------------------------------------ downloading


def test_a_package_is_downloaded_and_kept(tmp_path):
    site = FakeSite(published(("wireless", "7.16.1", "arm")))
    cache = cache_at(tmp_path, site)
    fetched = cache.fetch("wireless", V("7.16.1"), "arm", "arm")
    assert not fetched.cached and fetched.package.name == "wireless"
    assert fetched.package.path.name == "wireless-7.16.1-arm.npk"
    # ... and the second time nothing is asked for.
    again = cache.fetch("wireless", V("7.16.1"), "arm", "arm")
    assert again.cached and len(site.gets) == 1


def test_the_cache_reads_like_an_update_directory(tmp_path):
    """Flat, and holding files exactly as MikroTik publishes them, so the same scanner
    reads it."""
    site = FakeSite(published(("routeros", "7.16.1", "arm"), ("wireless", "7.16.1", "arm")))
    cache = cache_at(tmp_path, site)
    cache.fetch("routeros", V("7.16.1"), "arm", "arm")
    cache.fetch("wireless", V("7.16.1"), "arm", "arm")
    assert sorted(p.display_name for p in cache.scan().packages) == ["routeros", "wireless"]


def test_an_error_page_saved_under_the_right_name_is_thrown_away(tmp_path):
    """The failure that matters: it would upload, the device would reboot, and the radio
    would be gone."""
    url = npk_url("wireless", V("7.16.1"), "arm")
    site = FakeSite({url: b"<html>404 not found</html>"})
    cache = cache_at(tmp_path, site)
    with pytest.raises(DownloadFailed) as caught:
        cache.fetch("wireless", V("7.16.1"), "arm", "arm")
    assert caught.value.issue.code == "download.not_a_package"
    assert not list((tmp_path / "cache").glob("*.npk"))
    assert not list((tmp_path / "cache").glob("*.part"))


def test_a_package_of_the_wrong_version_is_thrown_away(tmp_path):
    """MikroTik's own name on the file, the wrong package inside it. The header decides."""
    url = npk_url("wireless", V("7.16.1"), "arm")
    site = FakeSite({url: make_npk("wireless", (7, 15, 3), "arm")})
    cache = cache_at(tmp_path, site)
    with pytest.raises(DownloadFailed) as caught:
        cache.fetch("wireless", V("7.16.1"), "arm", "arm")
    assert caught.value.issue.code == "download.wrong_package"
    assert "7.15.3" in caught.value.issue.message
    assert not list((tmp_path / "cache").glob("*"))


def test_a_package_for_the_wrong_architecture_is_thrown_away(tmp_path):
    url = npk_url("wireless", V("7.16.1"), "arm")
    site = FakeSite({url: make_npk("wireless", (7, 16, 1), "mmips")})
    cache = cache_at(tmp_path, site)
    with pytest.raises(DownloadFailed) as caught:
        cache.fetch("wireless", V("7.16.1"), "arm", "arm")
    assert caught.value.issue.code == "download.wrong_package"


def test_a_version_mikrotik_does_not_publish_says_so(tmp_path):
    cache = cache_at(tmp_path, FakeSite())
    with pytest.raises(DownloadFailed) as caught:
        cache.fetch("wifi-qcom", V("7.99.9"), "arm", "arm")
    assert caught.value.issue.code == "download.not_published"


def test_a_404_is_not_retried(tmp_path):
    """The server answered, and the answer was no."""
    site = FakeSite()
    cache = PackageCache(tmp_path / "cache", opener=site, retries=3)
    with pytest.raises(DownloadFailed):
        cache.fetch("wireless", V("7.16.1"), "arm", "arm")
    assert len(site.gets) == 1


def test_a_dropped_connection_is_retried(tmp_path):
    data = published(("wireless", "7.16.1", "arm"))
    url = next(iter(data))
    attempts = {"n": 0}

    def flaky(request: urllib.request.Request, timeout: float):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.URLError("connection reset")
        return FakeResponse(data[url])

    cache = PackageCache(tmp_path / "cache", opener=flaky, retries=2)
    assert cache.fetch("wireless", V("7.16.1"), "arm", "arm").package.name == "wireless"
    assert attempts["n"] == 3


def test_progress_is_reported(tmp_path):
    site = FakeSite(published(("routeros", "7.16.1", "arm")))
    seen: list[tuple[str, int, int]] = []
    cache = PackageCache(tmp_path / "cache", opener=site,
                         on_progress=lambda f, done, total: seen.append((f, done, total)))
    cache.fetch("routeros", V("7.16.1"), "arm", "arm")
    assert seen[0][1] == 0
    assert seen[-1][1] == seen[-1][2] > 0
    assert all(name == "routeros-7.16.1-arm.npk" for name, _, _ in seen)


def test_a_half_written_file_from_an_earlier_run_is_not_mistaken_for_the_package(tmp_path):
    cache_path = tmp_path / "cache"
    cache_path.mkdir()
    (cache_path / "wireless-7.16.1-arm.npk").write_bytes(
        make_npk("wireless", (7, 16, 1), "arm")[:12])  # cut off inside the header
    site = FakeSite(published(("wireless", "7.16.1", "arm")))
    cache = PackageCache(cache_path, opener=site)
    assert cache.have("wireless", V("7.16.1"), "arm", "arm") is None
    assert not cache.fetch("wireless", V("7.16.1"), "arm", "arm").cached


# ------------------------------------------------------------------ finding a stop


def test_the_newest_patch_of_a_line_is_found_by_asking_upward(tmp_path):
    site = FakeSite(published(("routeros", "7.12", "arm"), ("routeros", "7.12.1", "arm"),
                              ("routeros", "7.12.2", "arm")))
    cache = cache_at(tmp_path, site)
    assert str(cache.newest_patch(V("7.12.0"), "arm")) == "7.12.2"
    # 7.12.2 is the last hit, then two misses stop the walk.
    assert len(site.heads) == 5
    # ... and the answer is remembered.
    assert str(cache.newest_patch(V("7.12.0"), "arm")) == "7.12.2"
    assert len(site.heads) == 5


def test_a_pulled_release_in_the_middle_does_not_hide_the_ones_above_it(tmp_path):
    site = FakeSite(published(("routeros", "7.12", "arm"), ("routeros", "7.12.2", "arm")))
    cache = cache_at(tmp_path, site)
    assert str(cache.newest_patch(V("7.12.0"), "arm")) == "7.12.2"


def test_a_line_that_does_not_exist_has_no_newest(tmp_path):
    cache = cache_at(tmp_path, FakeSite())
    assert cache.newest_patch(V("7.99.0"), "arm") is None


# ------------------------------------------------------------------ the two sources


def antenna(version: str = "7.15.3", **extra):
    return make_inventory(version, architecture="arm", free_hdd_space=256 * MB,
                          radios=make_radios(("wlan1", "wireless", "station-bridge")),
                          packages=[PackageInfo(name="routeros", version=version),
                                    PackageInfo(name="wireless", version=version)],
                          **extra)


def test_the_update_directory_wins_over_the_cache(tmp_path):
    """A file the user put next to the exe is the one they meant."""
    offline = tmp_path / "Update"
    offline.mkdir()
    mine = offline / "routeros-7.16.1-arm.npk"
    mine.write_bytes(make_npk("system", (7, 16, 1), "arm"))
    site = FakeSite(published(("routeros", "7.16.1", "arm")))
    cache = cache_at(tmp_path, site)
    cache.fetch("routeros", V("7.16.1"), "arm", "arm")

    source = PackageSource(offline, cache)
    found = source.available()
    mains = [p for p in found.packages if p.is_main and p.version == V("7.16.1")]
    assert len(mains) == 1 and mains[0].path == mine


def test_offline_reaches_nothing(tmp_path):
    offline = tmp_path / "Update"
    offline.mkdir()
    site = FakeSite(published(("routeros", "7.16.1", "arm")))
    source = PackageSource(offline, cache_at(tmp_path, site), download=False)
    found, issues = source.obtain(["routeros"], V("7.16.1"), "arm", "arm")
    assert not site.requests and not issues and not found.packages
    assert source.stops([V("7.12.0")], "arm") == []


def test_every_missing_package_is_reported_not_just_the_first(tmp_path):
    """The user wants the whole list of what is wrong with a target version."""
    offline = tmp_path / "Update"
    offline.mkdir()
    site = FakeSite(published(("routeros", "7.16.1", "arm")))
    source = PackageSource(offline, cache_at(tmp_path, site))
    found, issues = source.obtain(["routeros", "wireless", "gps"], V("7.16.1"), "arm", "arm")
    assert [i.code for i in issues] == ["download.not_published"] * 2
    assert {i.params["name"] for i in issues} == {"gps", "wireless"}
    assert [p.display_name for p in found.packages] == ["routeros"]


def test_what_to_download_is_decided_before_any_file_exists():
    """The set comes from the device's state, so it can be named with nothing on disk."""
    names, issues = wanted_packages(antenna(), V("7.16.1"))
    assert names == {"routeros", "wireless"} and not issues


def test_a_wifi_device_asks_for_its_own_driver_and_the_extras_it_has():
    device = make_inventory("7.15.3", architecture="arm", free_hdd_space=256 * MB,
                            model="C52iG-5HaxD2HaxD",
                            radios=make_radios(("wifi1", "wifi", "ap")),
                            packages=[PackageInfo(name="routeros", version="7.15.3"),
                                      PackageInfo(name="wifi-qcom", version="7.15.3"),
                                      PackageInfo(name="container", version="7.15.3")])
    names, _ = wanted_packages(device, V("7.16.1"))
    assert names == {"routeros", "wifi-qcom", "container"}


def test_crossing_the_major_version_asks_for_the_driver_but_not_the_extras():
    device = make_inventory("6.48.6", architecture="arm", free_hdd_space=256 * MB,
                            radios=make_radios(("wlan1", "wireless", "ap-bridge")),
                            packages=[PackageInfo(name="routeros-arm", version="6.48.6"),
                                      PackageInfo(name="wireless", version="6.48.6",
                                                  bundle="routeros-arm")])
    names, _ = wanted_packages(device, V("7.16.1"))
    assert names == {"routeros", "wireless"}
    # Going the other way the bundle carries it, so there is one file to fetch.
    back, _ = wanted_packages(antenna("7.16.1"), V("6.49.22"))
    assert back == {"routeros"}


def test_an_unanswered_wifiwave2_split_is_reported_before_anything_is_downloaded():
    device = make_inventory("7.12.4", architecture="arm", free_hdd_space=256 * MB,
                            model="RBD53iG-5HacD2HnD-XX",
                            radios=make_radios(("wifi1", "wifi", "ap")),
                            packages=[PackageInfo(name="routeros", version="7.12.4"),
                                      PackageInfo(name="wifiwave2", version="7.12.4")])
    names, issues = wanted_packages(device, V("7.16.1"))
    assert [i.code for i in issues] == ["version.wifi_choice_needed"]
    assert names == {"routeros"}  # the driver is not named until the question is answered
    answered, no_issues = wanted_packages(device, V("7.16.1"),
                                          wifi_override=WirelessDriver.WIFI_QCOM_AC)
    assert answered == {"routeros", "wifi-qcom-ac"} and not no_issues


def test_a_downloaded_set_plans_a_route(tmp_path):
    """End to end on this PC's side: name what is needed, fetch it, plan from what is here."""
    from mqs.apply.version_change import plan_version

    offline = tmp_path / "Update"
    offline.mkdir()
    site = FakeSite(published(("routeros", "7.16.1", "arm"), ("wireless", "7.16.1", "arm")))
    source = PackageSource(offline, cache_at(tmp_path, site))
    device = antenna()
    names, _ = wanted_packages(device, V("7.16.1"))
    found, issues = source.obtain(names, V("7.16.1"), "arm", "arm")
    assert not issues
    plan = plan_version(device, found, V("7.16.1"))
    assert not plan.blocked
    assert sorted(p.display_name for p in plan.hops[0].packages) == ["routeros", "wireless"]
