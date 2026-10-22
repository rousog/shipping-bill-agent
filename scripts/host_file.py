"""启动本地静态服务 + cloudflared 临时隧道，给 OCR 提供公网 URL。

用法：
    python scripts/host_file.py
    python scripts/host_file.py --port 8000 --no-update-env

它会：
1. 在 data/assets 起一个静态 HTTP 服务；
2. 起 cloudflared quick tunnel；
3. 解析并打印 https://xxx.trycloudflare.com；
4. 默认把 ASSET_PUBLIC_BASE_URL 写进 .env；
5. Ctrl+C 同时停止服务和隧道。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

_CLOUDFLARED_CANDIDATES = (
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
)


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    for candidate in _CLOUDFLARED_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def update_env(url: str) -> None:
    if not ENV_PATH.exists():
        print(f"未找到 {ENV_PATH}，跳过 .env 更新")
        return
    text = ENV_PATH.read_text(encoding="utf-8")
    if re.search(r"^ASSET_PUBLIC_BASE_URL=.*$", text, flags=re.MULTILINE):
        text = re.sub(
            r"^ASSET_PUBLIC_BASE_URL=.*$",
            f"ASSET_PUBLIC_BASE_URL={url}",
            text,
            flags=re.MULTILINE,
        )
    else:
        text += f"\nASSET_PUBLIC_BASE_URL={url}\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    print(f"已更新 .env: ASSET_PUBLIC_BASE_URL={url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="本地文件 + cloudflared 隧道")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--asset-root",
        default=str(ROOT / "data" / "assets"),
        help="静态服务根目录，默认 data/assets",
    )
    parser.add_argument(
        "--no-update-env",
        action="store_true",
        help="不自动写 .env",
    )
    args = parser.parse_args()

    asset_root = Path(args.asset_root).resolve()
    if not asset_root.exists():
        print(f"目录不存在: {asset_root}", file=sys.stderr)
        return 1

    cloudflared = find_cloudflared()
    if not cloudflared:
        print(
            "未找到 cloudflared。请先安装：\n"
            "  winget install --id Cloudflare.cloudflared --source winget",
            file=sys.stderr,
        )
        return 1

    print(f"静态服务: http://localhost:{args.port} -> {asset_root}")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(args.port),
            "--directory",
            str(asset_root),
        ]
    )
    tunnel = subprocess.Popen(
        [
            cloudflared,
            "tunnel",
            "--url",
            f"http://localhost:{args.port}",
            "--no-autoupdate",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    url: str | None = None
    try:
        assert tunnel.stdout is not None
        for line in tunnel.stdout:
            print(line, end="")
            match = re.search(
                r"https://[a-z0-9-]+\.trycloudflare\.com", line
            )
            if match:
                url = match.group(0)
                print(f"\n公网 URL: {url}\n")
                if not args.no_update_env:
                    update_env(url)
                break
        if url is None:
            print("未能从 cloudflared 输出解析到 URL", file=sys.stderr)
        tunnel.wait()
    except KeyboardInterrupt:
        print("\n正在停止...")
    finally:
        for process in (tunnel, server):
            if process.poll() is None:
                process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
