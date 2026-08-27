from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_README_CHARS = 1000
MAX_VIDEO_BYTES = 200 * 1024 * 1024


def create_submission_zip(
    *,
    readme: str | Path,
    video: str | Path,
    output: str | Path,
    force: bool = False,
) -> Path:
    readme_path = Path(readme).expanduser().resolve()
    video_path = Path(video).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()

    if not readme_path.is_file():
        raise ValueError(f"README.txt does not exist: {readme_path}")

    try:
        readme_text = readme_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("README.txt must be valid UTF-8") from exc

    if not readme_text or len(readme_text) > MAX_README_CHARS:
        raise ValueError(
            f"README.txt must contain 1-{MAX_README_CHARS} characters"
        )

    if not video_path.is_file():
        raise ValueError(f"video does not exist: {video_path}")

    if video_path.suffix.lower() != ".mp4":
        raise ValueError("video must use the .mp4 suffix")

    if video_path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError("video must not exceed 200 MB")

    if output_path.suffix.lower() != ".zip":
        raise ValueError("output must use the .zip suffix")

    if output_path.exists() and not force:
        raise FileExistsError(
            f"output already exists: {output_path}; use --force to replace it"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    with zipfile.ZipFile(
        output_path,
        mode=mode,
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.write(readme_path, arcname="README.txt")
        archive.write(video_path, arcname=video_path.name)

    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the two-file submission archive."
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing validated .zip output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        output = create_submission_zip(
            readme=PROJECT_ROOT / "README.txt",
            video=args.video,
            output=args.output,
            force=args.force,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Created: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
