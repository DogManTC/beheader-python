#!/usr/bin/env python3
"""Polyglot generator for media files (Python port)."""

import argparse
import json
import os
import random
import shutil
import string
import subprocess
import tempfile
from pathlib import Path
from typing import List

from PIL import Image


def number_to_4b_le(num: int) -> bytes:
    return int(num).to_bytes(4, "little")


def number_to_4b_be(num: int) -> bytes:
    return int(num).to_bytes(4, "big")


def find_sub_array_index(data: bytes, sub: bytes, start: int = 0) -> int:
    idx = data.find(sub, start)
    return idx


def pad_left(s: int, target_len: int, pad_char: str = "0") -> str:
    s = str(s)
    return pad_char * max(0, target_len - len(s)) + s


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def convert_image(image_path: Path, out_path: Path) -> None:
    img = Image.open(image_path).convert("RGBA")
    img.save(out_path, format="PNG")


def build_skip_atom(png_path: Path, html_string: str) -> bytes:
    png_bytes = png_path.read_bytes()
    html_bytes = html_string.encode("utf-8") if html_string else b""
    skip_data = html_bytes + png_bytes
    skip_head = number_to_4b_be(len(skip_data) + 8) + b"skip"
    return skip_head + skip_data


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="beheader",
        description="Polyglot generator for media files.",
        usage="beheader <output> <image> <video|audio> [-options] [appendable...]",
        add_help=False,
    )
    parser.add_argument("output")
    parser.add_argument("image")
    parser.add_argument("video")
    parser.add_argument("appendable", nargs="*")
    parser.add_argument("-h", "--html", dest="html")
    parser.add_argument("-p", "--pdf", dest="pdf")
    parser.add_argument("-z", "--zip", dest="zips", action="append", default=[])
    parser.add_argument("-e", "--extra", dest="extra")
    parser.add_argument("--help", action="help", help="Print this help message and exit")
    args = parser.parse_args(argv)

    output = Path(args.output)
    image = Path(args.image)
    video = Path(args.video)
    appendables = [Path(p) for p in args.appendable]
    html = Path(args.html) if args.html else None
    pdf = Path(args.pdf) if args.pdf else None
    zip_paths = [Path(p) for p in args.zips]
    extra_bytes = Path(args.extra).read_bytes() if args.extra else b""

    tmpdir = Path(tempfile.mkdtemp())
    try:
        png_path = tmpdir / "img.png"
        convert_image(image, png_path)

        ftyp_buffer = bytearray(256 + 32)
        ftyp_buffer[2] = 1
        ftyp_buffer[3] = 32
        ftyp_buffer[4:8] = b"ftyp"
        ftyp_buffer[256:288] = bytes([
            0x00, 0x00, 0x00, 0x20, 0x66, 0x74, 0x79, 0x70,
            0x69, 0x73, 0x6f, 0x6d, 0x00, 0x00, 0x02, 0x00,
            0x69, 0x73, 0x6f, 0x6d, 0x69, 0x73, 0x6f, 0x32,
            0x61, 0x76, 0x63, 0x31, 0x6d, 0x70, 0x34, 0x31,
        ])
        ftyp_buffer[12] = 32
        ftyp_buffer[14:18] = number_to_4b_le(png_path.stat().st_size)

        # determine if input has video stream
        probe = run([
            "ffprobe", "-v", "error", "-select_streams", "v",
            "-show_entries", "stream=codec_type", "-of", "json", str(video)
        ])
        is_video = bool(json.loads(probe.stdout.decode("utf-8"))["streams"])

        mp4_path = tmpdir / "orig.mp4"
        if is_video:
            run([
                "ffmpeg", "-y", "-i", str(video), "-c:v", "libx264", "-strict", "-2",
                "-preset", "slow", "-pix_fmt", "yuv420p",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-f", "mp4", str(mp4_path)
            ])
        else:
            run([
                "ffmpeg", "-y", "-i", str(video), "-c:a", "aac", "-b:a", "192k", str(mp4_path)
            ])

        html_string = ""
        if html:
            html_string = (
                "--><style>body{font-size:0}</style><div style=font-size:initial>"
                + html.read_text()
                + "</div><!--"
            )

        skip_atom = build_skip_atom(png_path, html_string)

        orig_bytes = mp4_path.read_bytes()
        orig_ftyp_size = int.from_bytes(orig_bytes[0:4], "big")
        rest_bytes = orig_bytes[orig_ftyp_size:]

        png_offset = len(ftyp_buffer) + len(skip_atom[:8]) + len(html_string.encode("utf-8"))
        ftyp_buffer[18:22] = number_to_4b_le(png_offset)
        ftyp_buffer[4:8] = bytes([1, 0, 0, 0])
        ftyp_buffer[240:256] = b"isomiso2avc1mp41"

        atom_free_addr = 22
        ftyp_buffer[atom_free_addr:atom_free_addr + len(extra_bytes)] = extra_bytes
        atom_free_addr += len(extra_bytes)
        ftyp_buffer[22 + len(extra_bytes):22 + len(extra_bytes) + 4] = b"<!--"
        atom_free_addr += 4

        if pdf:
            pdf_bytes = pdf.read_bytes()
            mp4_size = len(ftyp_buffer) + len(skip_atom) + len(rest_bytes)
            ftyp_buffer[atom_free_addr] = 0x0A
            ftyp_buffer[atom_free_addr + 1:atom_free_addr + 10] = pdf_bytes[:9]
            atom_free_addr += 10
            offset = 30 + len(str(mp4_size))
            while True:
                offset -= 1
                obj_string = (
                    f"\n1 0 obj\n<</Length {mp4_size - atom_free_addr - len(extra_bytes) - offset}>>\nstream\n"
                )
                if offset == len(obj_string):
                    break
            obj_bytes = obj_string.encode("utf-8")
            start = atom_free_addr + len(extra_bytes)
            ftyp_buffer[start:start + len(obj_bytes)] = obj_bytes
            atom_free_addr += len(obj_bytes)

        final_mp4 = bytes(ftyp_buffer) + skip_atom + rest_bytes
        output.write_bytes(final_mp4)
        with output.open("r+b") as outf:
            outf.seek(3)
            outf.write(b"\x00")

        if pdf:
            object_terminator = b"\nendstream\nendobj\n"
            pdf_buffer = bytearray(object_terminator)
            pdf_buffer.extend(pdf_bytes)
            xref_start = find_sub_array_index(pdf_buffer, b"\nxref") + 1
            offset_start = find_sub_array_index(pdf_buffer, b"\n0000000000", xref_start) + 1
            startxref_start = find_sub_array_index(pdf_buffer, b"\nstartxref", xref_start) + 1
            startxref_end = pdf_buffer.find(b"\x0A", startxref_start + 11)
            try:
                if min(xref_start, offset_start, startxref_start, startxref_end) <= 0:
                    raise Exception("Failed to find xref table")
                out_size = output.stat().st_size + len(object_terminator)
                xref_header = pdf_buffer[xref_start:offset_start].decode("utf-8")
                count = int(xref_header.strip().replace("\n", " ").split(" ")[-1])
                curr = offset_start
                for _ in range(count):
                    offset = int(pdf_buffer[curr:curr+10].decode("utf-8").strip())
                    new_offset = offset + out_size
                    pdf_buffer[curr:curr+10] = pad_left(new_offset, 10)[:10].encode("utf-8")
                    curr = pdf_buffer.find(b"\x0A", curr + 1) + 1
                startxref = int(pdf_buffer[startxref_start + 10:startxref_end].decode("utf-8").strip())
                new_startxref = str(startxref + out_size)
                pdf_buffer[startxref_start + 10:startxref_start + 10 + len(new_startxref)] = new_startxref.encode("utf-8")
                pdf_buffer[startxref_start + 10 + len(new_startxref):startxref_start + 15 + len(new_startxref)] = b"\n%%EOF\n"
                for i in range(startxref_start + len(new_startxref) + 17, len(pdf_buffer)):
                    pdf_buffer[i] = 0
            except Exception as e:  # noqa: BLE001
                print(e)
                print("WARNING: Failed to fix PDF offsets. This is probably still fine.")
            with output.open("ab") as outf:
                outf.write(pdf_buffer)

        with output.open("ab") as outf:
            for path in appendables:
                if path.exists():
                    outf.write(path.read_bytes())

        if zip_paths:
            zip_dir = tmpdir / "zipdir"
            zip_dir.mkdir()
            for z in zip_paths:
                shutil.unpack_archive(str(z), str(zip_dir), format="zip")
            merged_zip = tmpdir / "merged.zip"
            shutil.make_archive(str(merged_zip.with_suffix("")), 'zip', str(zip_dir))
            with merged_zip.open("rb") as zf, output.open("ab") as outf:
                shutil.copyfileobj(zf, outf)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
