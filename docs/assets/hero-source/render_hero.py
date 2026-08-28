"""Render the 1280x640, 12-second VeriTrace README hero.

Color roles: cyan for actions, green for success/additions, red for failures and
deletions, gray for metadata, and light gray for primary text. The sequence is
based on the reproducible limit=0 regression demo. It requires only Python's
standard library plus an existing FFmpeg installation with drawtext support.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


WIDTH = 1280
HEIGHT = 640
FPS = 12
DURATION = 12.0
PREVIEW_AT = 11.4
FINAL_TEXT_END = 12.3

ROOT = Path(__file__).resolve().parents[3]
ASSET_DIR = ROOT / "docs" / "assets"
GIF_PATH = ASSET_DIR / "veritrace-hero.gif"
PREVIEW_PATH = ASSET_DIR / "veritrace-hero-preview.png"

COLORS = {
    "background": "0x0D1117",
    "surface": "0x111820",
    "border": "0x2A3745",
    "text": "0xE6EDF3",
    "muted": "0x8B98A5",
    "cyan": "0x46C2CB",
    "green": "0x56D364",
    "red": "0xFF6B6B",
    "yellow": "0xDDBB62",
}


def find_font() -> Path | None:
    candidates = (
        Path("C:/Windows/Fonts/CascadiaMono.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("/System/Library/Fonts/Menlo.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    )
    return next((path for path in candidates if path.is_file()), None)


def find_symbol_font() -> Path | None:
    candidates = (
        Path("C:/Windows/Fonts/seguisym.ttf"),
        Path("/System/Library/Fonts/Apple Symbols.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    return next((path for path in candidates if path.is_file()), None)


def escape_filter_value(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
    )


def fade_alpha(start: float, end: float, fade: float = 0.18) -> str:
    return (
        f"if(lt(t,{start:.2f}),0,"
        f"if(lt(t,{start + fade:.2f}),(t-{start:.2f})/{fade:.2f},"
        f"if(lt(t,{end - fade:.2f}),1,max(0,( {end:.2f}-t)/{fade:.2f}))))"
    ).replace("( ", "(")


def draw_text(
    text: str,
    *,
    x: int | str,
    y: int | str,
    size: int,
    color: str,
    start: float,
    end: float,
    font: Path | None,
    bold: bool = False,
    fade: float = 0.18,
) -> str:
    font_option = "font='monospace'"
    if font is not None:
        font_path = escape_filter_value(font.as_posix())
        font_option = f"fontfile='{font_path}'"

    border = ":borderw=1:bordercolor=0x000000@0.28" if bold else ""
    return (
        f"drawtext={font_option}:text='{escape_filter_value(text)}':"
        f"x={x}:y={y}:fontsize={size}:fontcolor={color}:"
        f"alpha='{fade_alpha(start, end, fade)}':expansion=none{border}"
    )


def draw_box(
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
    thickness: int | str,
    start: float,
    end: float,
) -> str:
    return (
        f"drawbox=x={x}:y={y}:w={width}:h={height}:color={color}:"
        f"t={thickness}:enable='between(t,{start:.2f},{end:.2f})'"
    )


def add_text(
    filters: list[str],
    font: Path | None,
    text: str,
    x: int | str,
    y: int | str,
    size: int,
    role: str,
    start: float,
    end: float,
    *,
    bold: bool = False,
    fade: float = 0.18,
) -> None:
    filters.append(
        draw_text(
            text,
            x=x,
            y=y,
            size=size,
            color=COLORS[role],
            start=start,
            end=end,
            font=font,
            bold=bold,
            fade=fade,
        )
    )


def add_workspace(filters: list[str], start: float, end: float) -> None:
    filters.extend(
        (
            draw_box(
                x=42,
                y=132,
                width=808,
                height=450,
                color=f"{COLORS['surface']}@0.97",
                thickness="fill",
                start=start,
                end=end,
            ),
            draw_box(
                x=42,
                y=132,
                width=808,
                height=450,
                color=f"{COLORS['border']}@0.95",
                thickness=2,
                start=start,
                end=end,
            ),
            draw_box(
                x=880,
                y=132,
                width=358,
                height=450,
                color=f"{COLORS['surface']}@0.88",
                thickness="fill",
                start=start,
                end=end,
            ),
            draw_box(
                x=880,
                y=132,
                width=358,
                height=450,
                color=f"{COLORS['border']}@0.85",
                thickness=2,
                start=start,
                end=end,
            ),
        )
    )


def add_top_brand(
    filters: list[str],
    font: Path | None,
    symbol_font: Path | None,
) -> None:
    add_text(
        filters,
        symbol_font or font,
        "✦ VeriTrace",
        44,
        38,
        34,
        "cyan",
        1.25,
        10.05,
        bold=True,
    )
    add_text(
        filters,
        font,
        "CONTROL · OBSERVE · VERIFY",
        900,
        48,
        17,
        "muted",
        1.25,
        10.05,
    )


def add_task(filters: list[str], font: Path | None, start: float, end: float) -> None:
    add_text(
        filters,
        font,
        "› Fix the limit=0 regression and verify the result.",
        68,
        163,
        25,
        "text",
        start,
        end,
    )


def add_flow(
    filters: list[str],
    font: Path | None,
    rows: list[tuple[str, str]],
    *,
    start: float,
    end: float,
) -> None:
    y = 185
    for index, (label, role) in enumerate(rows):
        add_text(filters, font, label, 920, y, 25, role, start + index * 0.06, end)
        y += 49


def build_filters(font: Path | None, symbol_font: Path | None) -> list[str]:
    filters: list[str] = ["format=rgb24"]

    # 0.0-1.5s: restrained brand reveal.
    add_text(
        filters,
        symbol_font or font,
        "✦ VeriTrace",
        "(w-text_w)/2",
        278,
        48,
        "muted",
        -0.60,
        1.48,
        bold=True,
    )
    add_text(
        filters,
        symbol_font or font,
        "✦",
        "(w-text_w)/2",
        202,
        64,
        "cyan",
        0.0,
        1.48,
        bold=True,
        fade=0.55,
    )
    add_text(
        filters,
        symbol_font or font,
        "✦ VeriTrace",
        "(w-text_w)/2",
        278,
        48,
        "text",
        0.0,
        1.48,
        bold=True,
        fade=0.55,
    )
    add_text(
        filters,
        font,
        "CONTROL · OBSERVE · VERIFY",
        "(w-text_w)/2",
        356,
        22,
        "cyan",
        0.62,
        1.48,
    )
    add_text(
        filters,
        font,
        "A lightweight local coding agent",
        "(w-text_w)/2",
        405,
        19,
        "muted",
        0.83,
        1.48,
    )

    add_workspace(filters, 1.38, 10.03)
    add_top_brand(filters, font, symbol_font)

    # 1.5-3.0s: task and model proposal.
    add_task(filters, font, 1.48, 3.03)
    add_text(filters, font, "model proposes an action", 900, 155, 18, "muted", 1.62, 3.03)
    add_flow(
        filters,
        font,
        [("Model", "text"), ("│", "muted"), ("▼", "muted"), ("ToolCall", "cyan")],
        start=1.70,
        end=3.03,
    )

    # 3.0-4.3s: local reads.
    add_task(filters, font, 2.96, 4.34)
    add_text(filters, font, "✓ Read  selector.py", 76, 252, 27, "green", 3.05, 4.34)
    add_text(
        filters,
        font,
        "✓ Read  tests/test_selector.py",
        76,
        307,
        27,
        "green",
        3.28,
        4.34,
    )
    add_text(filters, font, "local runtime executes", 900, 155, 18, "muted", 3.02, 4.34)
    add_flow(
        filters,
        font,
        [
            ("Model", "text"),
            ("│  ToolCall", "cyan"),
            ("▼", "muted"),
            ("Local Runtime", "cyan"),
        ],
        start=3.02,
        end=4.34,
    )

    # 4.3-5.8s: regression test edit.
    add_task(filters, font, 4.26, 5.84)
    add_text(filters, font, "● Edit  tests/test_selector.py", 76, 238, 26, "cyan", 4.34, 5.84)
    add_text(
        filters,
        font,
        "+ def test_zero_limit_returns_no_items():",
        92,
        306,
        23,
        "green",
        4.58,
        5.84,
    )
    add_text(
        filters,
        font,
        '+     assert select_items(["a", "b", "c"], 0) == []',
        92,
        352,
        21,
        "green",
        4.78,
        5.84,
    )
    add_text(filters, font, "✓ applied", 92, 420, 24, "green", 5.08, 5.84)
    add_text(filters, font, "controlled local edit", 900, 155, 18, "muted", 4.30, 5.84)
    add_flow(
        filters,
        font,
        [("ToolCall", "cyan"), ("│", "muted"), ("▼", "muted"), ("Local Runtime", "cyan")],
        start=4.30,
        end=5.84,
    )

    # 5.8-7.0s: a failed test becomes an observation.
    add_task(filters, font, 5.76, 7.04)
    add_text(filters, font, "● Run", 76, 238, 26, "cyan", 5.84, 7.04)
    add_text(filters, font, "$ python -m pytest -q", 92, 292, 24, "text", 5.96, 7.04)
    add_text(
        filters,
        symbol_font or font,
        "✗ 1 failed",
        92,
        365,
        28,
        "red",
        6.25,
        7.04,
        bold=True,
    )
    add_text(filters, font, "failure → observation", 900, 155, 18, "muted", 5.82, 7.04)
    add_flow(
        filters,
        font,
        [
            ("ToolResult", "cyan"),
            ("exit_code = 1", "red"),
            ("│", "muted"),
            ("▼", "muted"),
            ("Observation", "yellow"),
        ],
        start=5.82,
        end=7.04,
    )

    # 7.0-8.5s: smallest production fix.
    add_task(filters, font, 6.96, 8.54)
    add_text(filters, font, "● Edit  selector.py", 76, 238, 26, "cyan", 7.04, 8.54)
    add_text(filters, font, "- if not limit:", 92, 307, 25, "red", 7.28, 8.54)
    add_text(filters, font, "+ if limit is None:", 92, 354, 25, "green", 7.48, 8.54)
    add_text(filters, font, "✓ applied", 92, 423, 24, "green", 7.76, 8.54)
    add_text(filters, font, "evidence informs the next turn", 900, 155, 18, "muted", 7.02, 8.54)
    add_flow(
        filters,
        font,
        [("Observation", "yellow"), ("│", "muted"), ("▼", "muted"), ("next model turn", "text")],
        start=7.02,
        end=8.54,
    )

    # 8.5-10.0s: successful full-suite verification.
    add_task(filters, font, 8.46, 10.04)
    add_text(filters, font, "● Run", 76, 238, 26, "cyan", 8.54, 10.04)
    add_text(filters, font, "$ python -m pytest -q", 92, 292, 24, "text", 8.66, 10.04)
    add_text(filters, font, "✓ 3 passed", 92, 365, 28, "green", 8.94, 10.04, bold=True)
    add_text(filters, font, "result becomes evidence", 900, 155, 18, "muted", 8.52, 10.04)
    add_flow(
        filters,
        font,
        [
            ("ToolResult", "cyan"),
            ("exit_code = 0", "green"),
            ("│", "muted"),
            ("▼", "muted"),
            ("Verification", "green"),
        ],
        start=8.52,
        end=10.04,
    )

    # 10.0-12.0s: evidence-based conclusion.
    filters.extend(
        (
            draw_box(
                x=650,
                y=136,
                width=560,
                height=300,
                color=f"{COLORS['surface']}@0.98",
                thickness="fill",
                start=9.96,
                end=12.0,
            ),
            draw_box(
                x=650,
                y=136,
                width=560,
                height=300,
                color=f"{COLORS['green']}@0.72",
                thickness=2,
                start=9.96,
                end=12.0,
            ),
        )
    )
    add_text(filters, font, "Execution evidence", 72, 174, 19, "muted", 9.98, FINAL_TEXT_END)
    add_text(filters, font, "regression test", 76, 226, 20, "text", 10.02, FINAL_TEXT_END)
    add_text(
        filters,
        symbol_font or font,
        "✗ 1 failed",
        380,
        226,
        20,
        "red",
        10.08,
        FINAL_TEXT_END,
    )
    add_text(filters, font, "smallest fix", 76, 277, 20, "text", 10.14, FINAL_TEXT_END)
    add_text(filters, font, "✓ applied", 380, 277, 20, "green", 10.20, FINAL_TEXT_END)
    add_text(filters, font, "full test suite", 76, 328, 20, "text", 10.26, FINAL_TEXT_END)
    add_text(filters, font, "✓ 3 passed", 380, 328, 20, "green", 10.32, FINAL_TEXT_END)
    add_text(filters, font, "✓ Task completed", 696, 180, 36, "green", 10.04, FINAL_TEXT_END, bold=True)
    add_text(filters, font, "Final test run", 696, 272, 23, "muted", 10.20, FINAL_TEXT_END)
    add_text(filters, font, "✓ passed", 1010, 272, 23, "green", 10.26, FINAL_TEXT_END)
    add_text(filters, font, "Files changed", 696, 328, 23, "muted", 10.30, FINAL_TEXT_END)
    add_text(filters, font, "2", 1062, 328, 23, "text", 10.36, FINAL_TEXT_END)

    add_text(filters, font, "CONTROL", 250, 470, 20, "cyan", 10.34, FINAL_TEXT_END)
    add_text(filters, font, "OBSERVE", 570, 470, 20, "cyan", 10.52, FINAL_TEXT_END)
    add_text(filters, font, "VERIFY", 900, 470, 20, "cyan", 10.70, FINAL_TEXT_END)
    add_text(filters, font, "✓", 290, 510, 24, "green", 10.44, FINAL_TEXT_END)
    add_text(filters, font, "✓", 612, 510, 24, "green", 10.62, FINAL_TEXT_END)
    add_text(filters, font, "✓", 934, 510, 24, "green", 10.80, FINAL_TEXT_END)
    add_text(
        filters,
        symbol_font or font,
        "✦ VeriTrace",
        "(w-text_w)/2",
        554,
        29,
        "text",
        10.55,
        FINAL_TEXT_END,
        bold=True,
    )
    add_text(
        filters,
        font,
        "Model claims are not execution facts. Verification uses evidence.",
        "(w-text_w)/2",
        594,
        17,
        "muted",
        10.72,
        FINAL_TEXT_END,
    )

    return filters


def write_filter_graph(path: Path, filters: list[str], *, gif: bool) -> None:
    chain = "[0:v]" + ",\n".join(filters) + "[painted]"
    if gif:
        chain += (
            ";\n[painted]split[frames][palette_source];\n"
            "[palette_source]palettegen=max_colors=128:stats_mode=full[palette];\n"
            "[frames][palette]paletteuse=dither=bayer:bayer_scale=3:"
            "diff_mode=rectangle[output]"
        )
    else:
        chain += ";\n[painted]null[output]"
    path.write_text(chain + "\n", encoding="utf-8")


def run_ffmpeg(ffmpeg: str, filter_path: Path, output: Path, *, preview: bool) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={COLORS['background']}:s={WIDTH}x{HEIGHT}:r={FPS}:d={DURATION}",
    ]
    if preview:
        command.extend(["-ss", str(PREVIEW_AT)])
    command.extend(
        [
            "-filter_complex_script",
            str(filter_path),
            "-map",
            "[output]",
        ]
    )
    if preview:
        command.extend(["-frames:v", "1", "-update", "1"])
    else:
        command.extend(["-loop", "0"])
    command.append(str(output))
    subprocess.run(command, check=True)


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required but was not found on PATH")

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    filters = build_filters(find_font(), find_symbol_font())

    with tempfile.TemporaryDirectory(prefix="veritrace-hero-") as temporary:
        temp_dir = Path(temporary)
        gif_filter = temp_dir / "hero-gif.filter"
        preview_filter = temp_dir / "hero-preview.filter"
        write_filter_graph(gif_filter, filters, gif=True)
        write_filter_graph(preview_filter, filters, gif=False)
        run_ffmpeg(ffmpeg, gif_filter, GIF_PATH, preview=False)
        run_ffmpeg(ffmpeg, preview_filter, PREVIEW_PATH, preview=True)

    print(f"GIF: {GIF_PATH}")
    print(f"Preview: {PREVIEW_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
