"""Build script for packaging AutoYout into a standalone Windows application."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def build() -> int:
    print("=" * 60)
    print(" BẮT ĐẦU ĐÓNG GÓI ỨNG DỤNG AUTOYOUT (WINDOWS)")
    print("=" * 60)
    start_time = time.time()

    spec_file = ROOT / "AutoYout.spec"
    if not spec_file.is_file():
        print(f"[ERROR] Không tìm thấy file cấu hình: {spec_file}")
        return 1

    # Run PyInstaller
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec_file),
        "--noconfirm",
    ]
    print(f"Lệnh thực thi: {' '.join(cmd)}\n")

    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print("\n[ERROR] Quá trình đóng gói bằng PyInstaller thất bại!")
        return res.returncode

    dist_dir = ROOT / "dist" / "AutoYout"
    if not dist_dir.is_dir():
        print(f"\n[ERROR] Không tìm thấy thư mục dist: {dist_dir}")
        return 1

    # Post-build setup: ensure runtime folders exist in dist/AutoYout
    for folder in ("logs", "H2MTTS", "config/youtube_credentials"):
        (dist_dir / folder).mkdir(parents=True, exist_ok=True)

    # Copy config templates to top-level dist/AutoYout/config if present
    for cfg_file in ("default_settings.json", "language_prompt.json"):
        src = dist_dir / "_internal" / "config" / cfg_file
        dst = dist_dir / "config" / cfg_file
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)

    # Copy assets and bin to top-level dist/AutoYout for easy user access
    for item in ("assets", "bin"):
        src = dist_dir / "_internal" / item
        dst = dist_dir / item
        if src.exists() and not dst.exists():
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    elapsed = round(time.time() - start_time, 1)
    exe_path = dist_dir / "AutoYout.exe"

    print("\n" + "=" * 60)
    print(f" [THÀNH CÔNG] ĐÓNG GÓI HOÀN TẤT TRONG {elapsed}s!")
    print(f" Thư mục sản phẩm: {dist_dir}")
    print(f" File thực thi:    {exe_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
