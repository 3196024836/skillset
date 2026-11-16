"""
把 app/web/icon.svg 渲染成多尺寸 PNG，再打包成 assets/icon.ico。
用无头 Edge/Chrome 渲染（透明背景），不需要安装 Pillow 等依赖。

用法：python scripts/build_icon.py
"""

import base64
import html
import json
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

RootDir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RootDir / "app"))
from server import find_browser  # noqa: E402

# ICO 里包含的尺寸
Sizes = [16, 24, 32, 48, 64, 128, 256]


def render_pngs(svgPath: Path, sizes: list, workDir: Path) -> dict:
    """用无头浏览器的 canvas 把 SVG 渲染成多个尺寸的透明 PNG。

    SVG 以 data URL 内嵌，canvas 不会被污染；结果通过 --dump-dom 读回。

    :param svgPath: SVG 文件
    :param sizes: 边长列表（像素）
    :param workDir: 临时目录
    :returns: {尺寸: PNG 字节}
    """
    svgData = base64.b64encode(svgPath.read_bytes()).decode()
    page = workDir / "render.html"
    page.write_text(f"""<html><body><pre id=out></pre><script>
const img = new Image();
img.onload = () => {{
  const out = {{}};
  for (const size of {sizes}) {{
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, size, size);
    out[size] = canvas.toDataURL("image/png").split(",")[1];
  }}
  document.getElementById("out").textContent = JSON.stringify(out);
}};
img.src = "data:image/svg+xml;base64,{svgData}";
</script></body></html>""", encoding="utf-8")
    result = subprocess.run([find_browser(), "--headless=new", "--disable-gpu", f"--user-data-dir={workDir / 'profile'}",
                             "--virtual-time-budget=5000", "--dump-dom", page.as_uri()],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    match = re.search(r'<pre id="out">(.*?)</pre>', result.stdout, re.S)
    if not match or not match.group(1):
        raise RuntimeError("渲染失败：" + result.stderr[-500:])
    return {int(size): base64.b64decode(data) for size, data in json.loads(html.unescape(match.group(1))).items()}


def build_ico(pngs: list, output: Path) -> None:
    """把多个 PNG 打包成一个 ICO（ICO 允许直接内嵌 PNG）。

    :param pngs: [(尺寸, PNG 字节)]
    :param output: 输出 ICO
    """
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    output.write_bytes(header + entries + blobs)


def main() -> None:
    """渲染并打包。"""
    svgPath = RootDir / "app" / "web" / "icon.svg"
    assets = RootDir / "assets"
    assets.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        pngs = render_pngs(svgPath, Sizes + [512], Path(temp))
    build_ico([(size, pngs[size]) for size in Sizes], assets / "icon.ico")
    (assets / "icon-256.png").write_bytes(pngs[256])
    (assets / "icon-512.png").write_bytes(pngs[512])
    print("已生成 assets/icon.ico、icon-256.png、icon-512.png")


if __name__ == "__main__":
    main()
