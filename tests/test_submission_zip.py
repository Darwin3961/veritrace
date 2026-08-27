from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import create_submission_zip as submission


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    readme = tmp_path / "README.txt"
    readme.write_text("submission instructions", encoding="utf-8")
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake mp4 content")
    return readme, video, tmp_path / "submission.zip"


def test_valid_zip_contains_only_readme_and_video(tmp_path: Path):
    readme, video, output = _inputs(tmp_path)

    created = submission.create_submission_zip(
        readme=readme,
        video=video,
        output=output,
    )

    assert created == output.resolve()
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["README.txt", "demo.mp4"]
        assert archive.read("README.txt") == readme.read_bytes()
        assert archive.read("demo.mp4") == video.read_bytes()


def test_missing_video_is_rejected(tmp_path: Path):
    readme, _, output = _inputs(tmp_path)

    with pytest.raises(ValueError, match="video does not exist"):
        submission.create_submission_zip(
            readme=readme,
            video=tmp_path / "missing.mp4",
            output=output,
        )


def test_missing_readme_is_rejected(tmp_path: Path):
    _, video, output = _inputs(tmp_path)

    with pytest.raises(ValueError, match="README.txt does not exist"):
        submission.create_submission_zip(
            readme=tmp_path / "missing-README.txt",
            video=video,
            output=output,
        )


def test_non_mp4_video_is_rejected(tmp_path: Path):
    readme, _, output = _inputs(tmp_path)
    video = tmp_path / "demo.mov"
    video.write_bytes(b"video")

    with pytest.raises(ValueError, match=".mp4"):
        submission.create_submission_zip(
            readme=readme,
            video=video,
            output=output,
        )


def test_non_zip_output_is_rejected(tmp_path: Path):
    readme, video, _ = _inputs(tmp_path)

    with pytest.raises(ValueError, match=".zip"):
        submission.create_submission_zip(
            readme=readme,
            video=video,
            output=tmp_path / "submission.tar",
        )


def test_video_over_200_mb_is_rejected(monkeypatch, tmp_path: Path):
    readme, video, output = _inputs(tmp_path)
    original_stat = Path.stat
    video_text = str(video.resolve())

    def fake_stat(path, *args, **kwargs):
        real = original_stat(path, *args, **kwargs)
        if str(path) == video_text:
            return SimpleNamespace(
                st_mode=real.st_mode,
                st_size=submission.MAX_VIDEO_BYTES + 1,
            )
        return real

    monkeypatch.setattr(Path, "stat", fake_stat)

    with pytest.raises(ValueError, match="200 MB"):
        submission.create_submission_zip(
            readme=readme,
            video=video,
            output=output,
        )


def test_existing_output_requires_force(tmp_path: Path):
    readme, video, output = _inputs(tmp_path)
    submission.create_submission_zip(
        readme=readme,
        video=video,
        output=output,
    )

    with pytest.raises(FileExistsError):
        submission.create_submission_zip(
            readme=readme,
            video=video,
            output=output,
        )


def test_force_replaces_existing_output(tmp_path: Path):
    readme, video, output = _inputs(tmp_path)
    output.write_bytes(b"old archive")

    submission.create_submission_zip(
        readme=readme,
        video=video,
        output=output,
        force=True,
    )

    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["README.txt", "demo.mp4"]


def test_readme_over_limit_is_rejected(tmp_path: Path):
    readme, video, output = _inputs(tmp_path)
    readme.write_text("x" * 1001, encoding="utf-8")

    with pytest.raises(ValueError, match="1-1000"):
        submission.create_submission_zip(
            readme=readme,
            video=video,
            output=output,
        )


def test_archive_names_cannot_traverse(tmp_path: Path):
    nested = tmp_path / "nested"
    nested.mkdir()
    readme = nested / "README.txt"
    readme.write_text("readme", encoding="utf-8")
    video = nested / "demo.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "submission.zip"

    submission.create_submission_zip(
        readme=readme,
        video=video,
        output=output,
    )

    with zipfile.ZipFile(output) as archive:
        assert all("/" not in name and "\\" not in name for name in archive.namelist())
        assert all(name not in {".", ".."} for name in archive.namelist())


def test_runtime_and_secret_files_are_never_included(tmp_path: Path):
    readme, video, output = _inputs(tmp_path)
    (tmp_path / ".env").write_text("not packaged", encoding="utf-8")
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "session.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "eval-results.json").write_text("[]", encoding="utf-8")

    submission.create_submission_zip(
        readme=readme,
        video=video,
        output=output,
    )

    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"README.txt", "demo.mp4"}


def test_cli_creates_archive(monkeypatch, tmp_path: Path, capsys):
    readme, video, output = _inputs(tmp_path)
    monkeypatch.setattr(submission, "PROJECT_ROOT", readme.parent)

    assert submission.main([
        "--video",
        str(video),
        "--output",
        str(output),
    ]) == 0
    assert "Created:" in capsys.readouterr().out
