import io
import tarfile
from pathlib import Path

import pytest

import updater


def test_ensure_dirs_creates_only_configured_directories(
    updater_paths: dict[str, Path],
) -> None:
    updater.ensure_dirs()

    for name in ("cache", "install", "config", "logs"):
        assert updater_paths[name].is_dir()

    assert not updater_paths["autostart"].exists()
    assert not updater_paths["bin"].exists()


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "x64"),
        ("AMD64", "x64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
    ],
)
def test_get_system_arch_maps_machine_names(
    monkeypatch: pytest.MonkeyPatch, machine: str, expected: str
) -> None:
    monkeypatch.setattr(updater.platform, "machine", lambda: machine)

    assert updater.get_system_arch() == expected


def test_get_system_arch_rejects_unknown_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.platform, "machine", lambda: "unknown")

    with pytest.raises(RuntimeError, match="Unsupported system architecture"):
        updater.get_system_arch()


@pytest.mark.parametrize(
    ("tag_name", "backend", "expected_id", "expected_parsed"),
    [
        ("b9949", "cpu", "b9949", ("b9949", "cpu")),
        ("b9949", "vulkan", "b9949-vulkan", ("b9949", "vulkan")),
    ],
)
def test_version_id_round_trip(
    tag_name: str, backend: str, expected_id: str, expected_parsed: tuple[str, str]
) -> None:
    version_id = updater.get_version_id(tag_name, backend)

    assert version_id == expected_id
    assert updater.parse_version_id(version_id) == expected_parsed


def test_get_asset_for_backend_selects_matching_architecture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater, "get_system_arch", lambda: "x64")
    release = {
        "assets": [
            {
                "name": "llama-b9949-bin-ubuntu-x64.tar.gz",
                "browser_download_url": "https://example.invalid/cpu.tar.gz",
                "digest": "sha256:" + "a" * 64,
            },
            {
                "name": "llama-b9949-bin-ubuntu-vulkan-x64.tar.gz",
                "browser_download_url": "https://example.invalid/vulkan.tar.gz",
                "digest": "sha256:" + "b" * 64,
            },
            {
                "name": "llama-b9949-bin-ubuntu-vulkan-arm64.tar.gz",
                "browser_download_url": "https://example.invalid/arm64.tar.gz",
                "digest": "sha256:" + "c" * 64,
            },
        ]
    }

    assert updater.get_asset_for_backend(release, "cpu") == (
        "llama-b9949-bin-ubuntu-x64.tar.gz",
        "https://example.invalid/cpu.tar.gz",
        "a" * 64,
    )
    assert updater.get_asset_for_backend(release, "vulkan") == (
        "llama-b9949-bin-ubuntu-vulkan-x64.tar.gz",
        "https://example.invalid/vulkan.tar.gz",
        "b" * 64,
    )


def test_prepare_download_returns_metadata_for_matching_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater, "get_system_arch", lambda: "x64")
    releases = [
        {
            "tag_name": "b9949",
            "assets": [
                {
                    "name": "llama-b9949-bin-ubuntu-x64.tar.gz",
                    "browser_download_url": "https://example.invalid/cpu.tar.gz",
                    "digest": "sha256:" + "d" * 64,
                }
            ],
        }
    ]

    download_info, error = updater.prepare_download("b9949", "cpu", releases)

    assert error is None
    assert download_info == (
        "b9949",
        "https://example.invalid/cpu.tar.gz",
        "d" * 64,
    )


def test_prepare_download_rejects_unknown_release() -> None:
    download_info, error = updater.prepare_download("b9999", "cpu", [])

    assert download_info is None
    assert error == "Could not find metadata for this version."


def test_installed_versions_only_include_directories_with_server(
    updater_paths: dict[str, Path],
) -> None:
    install_dir = updater_paths["install"]
    valid_version = install_dir / "b9949-vulkan"
    invalid_version = install_dir / "b9999"
    regular_file = install_dir / "not-a-version"

    valid_version.mkdir(parents=True)
    (valid_version / "llama-server").touch()
    invalid_version.mkdir()
    regular_file.touch()

    assert updater.is_version_installed("b9949", "vulkan")
    assert not updater.is_version_installed("b9999", "cpu")
    assert updater.get_installed_versions() == ["b9949-vulkan"]


def _create_executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> None:
    path.write_text(content)
    path.chmod(0o755)


def _add_regular_file(
    archive: tarfile.TarFile, name: str, content: bytes, mode: int = 0o644
) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def _add_symlink(archive: tarfile.TarFile, name: str, target: str) -> None:
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    archive.addfile(member)


def test_manage_symlinks_preserves_external_files_and_removes_owned_links(
    updater_paths: dict[str, Path],
) -> None:
    install_dir = updater_paths["install"]
    bin_dir = updater_paths["bin"]
    version_dir = install_dir / "b9949-vulkan"
    version_dir.mkdir(parents=True)
    _create_executable(version_dir / "llama-server")
    _create_executable(version_dir / "llama-cli")
    _create_executable(version_dir / "llama-extra")
    _create_executable(version_dir / "libllama-test.so")

    bin_dir.mkdir()
    external_file = bin_dir / "llama-extra"
    external_file.write_text("do not replace")

    result = updater.manage_symlinks("b9949-vulkan", enabled=True)

    assert not result
    assert result.conflicts == ["llama-extra"]
    assert external_file.read_text() == "do not replace"
    assert (bin_dir / "llama-server").resolve() == version_dir / "llama-server"
    assert (bin_dir / "llama-cli").resolve() == version_dir / "llama-cli"
    assert not (bin_dir / "libllama-test.so").exists()

    result = updater.manage_symlinks(None, enabled=False)

    assert result
    assert external_file.exists()
    assert not (bin_dir / "llama-server").exists()
    assert not (bin_dir / "llama-cli").exists()


def test_manage_symlinks_never_removes_third_party_symlinks(
    updater_paths: dict[str, Path],
) -> None:
    bin_dir = updater_paths["bin"]
    external_target = updater_paths["config"] / "external-tool"
    external_target.parent.mkdir()
    _create_executable(external_target)
    bin_dir.mkdir()
    third_party_link = bin_dir / "external-tool"
    third_party_link.symlink_to(external_target)

    assert updater.manage_symlinks(None, enabled=False)

    assert third_party_link.is_symlink()
    assert third_party_link.resolve() == external_target


def test_manage_symlinks_keeps_existing_links_when_version_is_not_installed(
    updater_paths: dict[str, Path],
) -> None:
    install_dir = updater_paths["install"]
    version_dir = install_dir / "b9949-vulkan"
    version_dir.mkdir(parents=True)
    _create_executable(version_dir / "llama-server")

    assert updater.manage_symlinks("b9949-vulkan", enabled=True)
    existing_link = updater.BIN_LINK_DIR / "llama-server"

    result = updater.manage_symlinks("b9999-vulkan", enabled=True)

    assert not result
    assert existing_link.resolve() == version_dir / "llama-server"


def test_manage_symlinks_removes_links_unique_to_previous_version(
    updater_paths: dict[str, Path],
) -> None:
    install_dir = updater_paths["install"]
    old_version = install_dir / "b9948-vulkan"
    new_version = install_dir / "b9949-vulkan"
    old_version.mkdir(parents=True)
    new_version.mkdir()
    _create_executable(old_version / "llama-server")
    _create_executable(old_version / "llama-old-tool")
    _create_executable(new_version / "llama-server")
    _create_executable(new_version / "llama-new-tool")

    assert updater.manage_symlinks("b9948-vulkan", enabled=True)
    assert (updater.BIN_LINK_DIR / "llama-old-tool").is_symlink()

    assert updater.manage_symlinks("b9949-vulkan", enabled=True)

    assert not (updater.BIN_LINK_DIR / "llama-old-tool").exists()
    assert (updater.BIN_LINK_DIR / "llama-new-tool").resolve() == (
        new_version / "llama-new-tool"
    )


def test_extract_archive_preserves_internal_library_symlink_chain(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "llama.tar.gz"
    destination = tmp_path / "extracted"
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_regular_file(
            archive, "llama-b9949/llama-server", b"#!/bin/sh\nexit 0\n", mode=0o755
        )
        _add_regular_file(archive, "llama-b9949/libllama.so.0.0.9949", b"library")
        _add_symlink(archive, "llama-b9949/libllama.so.0", "libllama.so.0.0.9949")
        _add_symlink(archive, "llama-b9949/libllama.so", "libllama.so.0")
        _add_regular_file(archive, "llama-b9949/LICENSE", b"license")
        _add_regular_file(archive, "llama-b9949/subdir/data", b"data")

    updater.extract_archive_safely(archive_path, destination)

    assert (destination / "llama-server").is_file()
    assert (destination / "llama-server").stat().st_mode & 0o111
    assert (destination / "libllama.so").is_symlink()
    assert (
        destination / "libllama.so"
    ).resolve() == destination / "libllama.so.0.0.9949"
    assert (destination / "LICENSE").read_bytes() == b"license"
    assert (destination / "subdir" / "data").read_bytes() == b"data"


@pytest.mark.parametrize(
    ("name", "link_target"),
    [
        ("../outside", None),
        ("/tmp/outside", None),
        ("llama-b9949/unsafe", "/tmp/outside"),
        ("llama-b9949/unsafe", "../../outside"),
    ],
)
def test_extract_archive_rejects_unsafe_paths(
    tmp_path: Path, name: str, link_target: str | None
) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    destination = tmp_path / "extracted"
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_regular_file(
            archive, "llama-b9949/llama-server", b"#!/bin/sh\nexit 0\n", mode=0o755
        )
        if link_target is None:
            _add_regular_file(archive, name, b"unsafe")
        else:
            _add_symlink(archive, name, link_target)

    with pytest.raises(RuntimeError):
        updater.extract_archive_safely(archive_path, destination)

    assert not (tmp_path / "outside").exists()


def test_extract_archive_rejects_broken_internal_symlink(tmp_path: Path) -> None:
    archive_path = tmp_path / "broken-link.tar.gz"
    destination = tmp_path / "extracted"
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_regular_file(
            archive, "llama-b9949/llama-server", b"#!/bin/sh\nexit 0\n", mode=0o755
        )
        _add_symlink(archive, "llama-b9949/libllama.so", "missing-library.so")

    with pytest.raises(RuntimeError, match="Invalid internal symlink"):
        updater.extract_archive_safely(archive_path, destination)


def test_extract_archive_rejects_special_files(tmp_path: Path) -> None:
    archive_path = tmp_path / "special-file.tar.gz"
    destination = tmp_path / "extracted"
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_regular_file(
            archive, "llama-b9949/llama-server", b"#!/bin/sh\nexit 0\n", mode=0o755
        )
        special = tarfile.TarInfo("llama-b9949/unsafe-fifo")
        special.type = tarfile.FIFOTYPE
        archive.addfile(special)

    with pytest.raises(RuntimeError, match="Unsupported archive member type"):
        updater.extract_archive_safely(archive_path, destination)


def test_failed_extraction_preserves_installed_version(
    updater_paths: dict[str, Path],
) -> None:
    install_dir = updater_paths["install"]
    target_dir = install_dir / "b9949-vulkan"
    target_dir.mkdir(parents=True)
    _create_executable(target_dir / "llama-server", "old version")

    archive_path = updater_paths["cache"] / "invalid.tar.gz"
    archive_path.parent.mkdir()
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_regular_file(archive, "llama-b9950/not-server", b"invalid")

    staging_dir = install_dir / ".b9950.staging"
    updater.extract_archive_safely(archive_path, staging_dir)

    assert (target_dir / "llama-server").read_text() == "old version"
    assert (staging_dir / "not-server").read_bytes() == b"invalid"


def test_publish_installation_replaces_version_only_after_staging_is_valid(
    updater_paths: dict[str, Path],
) -> None:
    install_dir = updater_paths["install"]
    target_dir = install_dir / "b9949-vulkan"
    staging_dir = install_dir / ".b9949.staging"
    target_dir.mkdir(parents=True)
    staging_dir.mkdir(parents=True)
    _create_executable(target_dir / "llama-server", "old version")
    _create_executable(staging_dir / "llama-server", "new version")

    updater.publish_installation(staging_dir, target_dir)

    assert (target_dir / "llama-server").read_text() == "new version"
    assert not staging_dir.exists()
    assert not list(install_dir.glob(".b9949-vulkan.backup-*"))


def test_publish_installation_restores_previous_version_after_failure(
    monkeypatch: pytest.MonkeyPatch, updater_paths: dict[str, Path]
) -> None:
    install_dir = updater_paths["install"]
    target_dir = install_dir / "b9949-vulkan"
    staging_dir = install_dir / ".b9949.staging"
    target_dir.mkdir(parents=True)
    staging_dir.mkdir()
    _create_executable(target_dir / "llama-server", "old version")
    _create_executable(staging_dir / "llama-server", "new version")

    real_replace = updater.os.replace

    def fail_staging_publish(source: Path | str, destination: Path | str) -> None:
        if Path(source) == staging_dir:
            raise OSError("simulated publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(updater.os, "replace", fail_staging_publish)

    with pytest.raises(OSError, match="simulated publish failure"):
        updater.publish_installation(staging_dir, target_dir)

    assert (target_dir / "llama-server").read_text() == "old version"
    assert staging_dir.exists()
    assert not list(install_dir.glob(".b9949-vulkan.backup-*"))
