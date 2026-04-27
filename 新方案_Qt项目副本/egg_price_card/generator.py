"""鸡蛋价格卡片生成器

从 JSON 数据生成品种零售价卡片和重量标准批发价卡片的 HTML 或 PNG。

用法:
    python generator.py --input data.json --format png
    python generator.py --format png          # 使用内置示例数据，输出 PNG
    python generator.py --format html          # 输出 HTML
    python generator.py                        # 默认输出 HTML
"""

import argparse
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VarietyItem:
    name: str
    price: float
    change: float  # 正数=涨, 负数=跌, 0=稳

    @property
    def trend(self) -> str:
        if self.change > 0:
            return "涨"
        if self.change < 0:
            return "跌"
        return "稳"

    @property
    def trend_color(self) -> str:
        if self.change > 0:
            return "#D32F2F"  # 红
        if self.change < 0:
            return "#2E7D32"  # 绿
        return "#333333"     # 黑

    @property
    def change_str(self) -> str:
        if self.change > 0:
            return f"+{self.change}"
        if self.change < 0:
            return str(self.change)
        return "0"


@dataclass
class WeightItem:
    weight: str
    price: float
    change: float

    @property
    def trend(self) -> str:
        if self.change > 0:
            return "涨"
        if self.change < 0:
            return "跌"
        return "稳"

    @property
    def trend_color(self) -> str:
        if self.change > 0:
            return "#D32F2F"
        if self.change < 0:
            return "#2E7D32"
        return "#333333"

    @property
    def change_str(self) -> str:
        if self.change > 0:
            return f"+{self.change}"
        if self.change < 0:
            return str(self.change)
        return "0"


@dataclass
class EggPriceData:
    date: str = ""
    varieties: list[VarietyItem] = field(default_factory=list)
    weight_standards: list[WeightItem] = field(default_factory=list)


def parse_json(data: dict) -> EggPriceData:
    result = EggPriceData(date=data.get("date", ""))
    for v in data.get("varieties", []):
        result.varieties.append(VarietyItem(**v))
    for w in data.get("weight_standards", []):
        result.weight_standards.append(WeightItem(**w))
    return result


SAMPLE_DATA = {
    "date": "2026/04/27",
    "varieties": [
        {"name": "粉壳蛋", "price": 3.7, "change": -0.1},
        {"name": "褐壳蛋", "price": 4.0, "change": -0.1},
        {"name": "富硒蛋", "price": 3.85, "change": -0.1},
        {"name": "保洁蛋", "price": 4.1, "change": -0.1},
    ],
    "weight_standards": [
        {"weight": "44", "price": 176, "change": -2},
        {"weight": "43", "price": 174, "change": -2},
        {"weight": "42", "price": 172, "change": -2},
        {"weight": "41", "price": 170, "change": -2},
        {"weight": "40", "price": 168, "change": -2},
        {"weight": "39", "price": 166, "change": -2},
        {"weight": "37-38", "price": 163, "change": -2},
        {"weight": "35-36", "price": 161, "change": -2},
        {"weight": "33-34", "price": 158, "change": -2},
        {"weight": "33以下", "price": 155, "change": -3},
    ],
}


# ── SVG Icons (全部视觉中心对齐到 y=24) ──────────────────────────────────────

ICON_EGG = """<svg width="48" height="48" viewBox="0 0 48 48" fill="none">
  <ellipse cx="24" cy="24" rx="13" ry="17" fill="#FFFFFF" stroke="#E0D0B8" stroke-width="1.5"/>
  <circle cx="24" cy="28" r="6.5" fill="#FFB300"/>
  <ellipse cx="21.5" cy="26" rx="2.2" ry="1.8" fill="#FFA000" opacity="0.4"/>
</svg>"""

ICON_CALC = """<svg width="48" height="48" viewBox="0 0 48 48" fill="none">
  <rect x="12" y="4" width="24" height="40" rx="4" fill="#FFFFFF" stroke="#E0D0B8" stroke-width="1.2"/>
  <rect x="15" y="8" width="18" height="9" rx="2" fill="#E8F5E9"/>
  <rect x="15" y="20" width="5" height="5" rx="1.2" fill="#FFECB3"/>
  <rect x="21.5" y="20" width="5" height="5" rx="1.2" fill="#FFECB3"/>
  <rect x="28" y="20" width="5" height="5" rx="1.2" fill="#FFECB3"/>
  <rect x="15" y="27" width="5" height="5" rx="1.2" fill="#FFECB3"/>
  <rect x="21.5" y="27" width="5" height="5" rx="1.2" fill="#FFECB3"/>
  <rect x="28" y="27" width="5" height="5" rx="1.2" fill="#FFECB3"/>
  <rect x="15" y="34" width="11.5" height="5" rx="1.2" fill="#FFECB3"/>
  <rect x="28" y="34" width="5" height="5" rx="1.2" fill="#FFB300"/>
</svg>"""

ICON_BASKET = """<svg width="48" height="48" viewBox="0 0 48 48" fill="none">
  <path d="M7 18 L11 40 L37 40 L41 18 Z" fill="#FFFFFF" stroke="#E0D0B8" stroke-width="1.2"/>
  <path d="M7 18 L41 18" stroke="#E0D0B8" stroke-width="2.5"/>
  <path d="M16 18 Q16 8 24 8 Q32 8 32 18" fill="none" stroke="#FFFFFF" stroke-width="3.5" stroke-linecap="round"/>
  <path d="M16 18 Q16 8 24 8 Q32 8 32 18" fill="none" stroke="#E0D0B8" stroke-width="2" stroke-linecap="round"/>
  <line x1="18" y1="21" x2="19.5" y2="38" stroke="#E0D0B8" stroke-width="1"/>
  <line x1="24" y1="21" x2="24" y2="38" stroke="#E0D0B8" stroke-width="1"/>
  <line x1="30" y1="21" x2="28.5" y2="38" stroke="#E0D0B8" stroke-width="1"/>
</svg>"""

ICON_TRUCK = """<svg width="48" height="48" viewBox="0 0 48 48" fill="none">
  <rect x="4" y="12" width="24" height="18" rx="3" fill="#FFFFFF" stroke="#E0D0B8" stroke-width="1.2"/>
  <path d="M28 16 L37 16 L43 24 L43 32 L28 32 Z" fill="#FFFFFF" stroke="#E0D0B8" stroke-width="1.2"/>
  <rect x="30.5" y="19" width="8.5" height="7.5" rx="2" fill="#E3F2FD"/>
  <circle cx="13" cy="34" r="4.5" fill="#FFFFFF" stroke="#BDBDBD" stroke-width="1.5"/>
  <circle cx="13" cy="34" r="2" fill="#BDBDBD"/>
  <circle cx="37" cy="34" r="4.5" fill="#FFFFFF" stroke="#BDBDBD" stroke-width="1.5"/>
  <circle cx="37" cy="34" r="2" fill="#BDBDBD"/>
</svg>"""


# ── Card Renderers ───────────────────────────────────────────────────────────

def _render_variety_card(data: EggPriceData) -> str:
    rows = ""
    for i, v in enumerate(data.varieties):
        row_bg = ' style="background:#FFF6ED;"' if i % 2 == 0 else ""
        rows += f"""
                <tr{row_bg}>
                    <td class="col-name">{v.name}</td>
                    <td class="col-price">{v.price}</td>
                    <td class="col-change">{v.change_str}</td>
                    <td class="col-trend" style="color:{v.trend_color}">{v.trend}</td>
                </tr>"""

    return f"""
        <div id="card-variety" class="card">
            <div class="card-top">
                <div class="icon-row">
                    {ICON_EGG}
                    {ICON_CALC}
                    {ICON_BASKET}
                </div>
            </div>
            <table class="ptable">
                <thead>
                    <tr>
                        <th>品种</th>
                        <th>价格<br><span class="sub">(元/斤)</span></th>
                        <th>较昨日</th>
                        <th>涨跌</th>
                    </tr>
                </thead>
                <tbody>
{rows}
                </tbody>
            </table>
        </div>"""


def _render_weight_card(data: EggPriceData) -> str:
    rows = ""
    for i, w in enumerate(data.weight_standards):
        row_bg = ' style="background:#FFF6ED;"' if i % 2 == 0 else ""
        rows += f"""
                <tr{row_bg}>
                    <td class="col-name">{w.weight}</td>
                    <td class="col-price">{int(w.price)}</td>
                    <td class="col-change">{w.change_str}</td>
                    <td class="col-trend" style="color:{w.trend_color}">{w.trend}</td>
                </tr>"""

    return f"""
        <div id="card-weight" class="card">
            <div class="card-top">
                <div class="icon-row">
                    {ICON_TRUCK}
                    {ICON_CALC}
                    {ICON_BASKET}
                </div>
            </div>
            <table class="ptable">
                <thead>
                    <tr>
                        <th>360枚/箱<br><span class="sub">净重</span></th>
                        <th>价格<br><span class="sub">(元/箱)</span></th>
                        <th>较昨日</th>
                        <th>涨跌</th>
                    </tr>
                </thead>
                <tbody>
{rows}
                </tbody>
            </table>
            <div class="card-bottom">
                <b>要求：</b>无药残、无双黄、脏蛋、裂纹沙壳
            </div>
        </div>"""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>鸡蛋价格卡片 - {date}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: "SimHei","黑体","Microsoft YaHei",sans-serif;
    background: {body_bg};
    display: flex;
    justify-content: center;
    align-items: flex-start;
    gap: 28px;
    padding: 40px 20px;
    flex-wrap: wrap;
}}

/* ── Card Shell ─────────────────────────────────── */
.card {{
    width: 360px;
    background: #FFF8F0;
    border-radius: 14px;
    border: 1.5px solid #D4C4B0;
    box-shadow: 0 3px 16px rgba(120,80,40,0.12);
    overflow: hidden;
}}

/* ── Top Band ───────────────────────────────────── */
.card-top {{
    background: linear-gradient(180deg, #FFBE76 0%, #F5A623 100%);
    padding: 18px 0 14px;
    text-align: center;
}}
.icon-row {{
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 24px;
}}
.icon-row svg {{
    display: block;
    vertical-align: middle;
}}

/* ── Table ──────────────────────────────────────── */
.ptable {{
    width: 100%;
    border-collapse: collapse;
}}
.ptable thead th {{
    background: #F0932B;
    color: #FFFFFF;
    font-size: 13.5px;
    font-weight: 600;
    padding: 9px 6px;
    text-align: center;
    line-height: 1.45;
    border-bottom: 2px solid #D4851F;
}}
.ptable thead th .sub {{
    font-size: 11px;
    font-weight: 400;
    opacity: 0.85;
}}
.ptable tbody td {{
    padding: 9px 6px;
    text-align: center;
    font-size: 13.5px;
    color: #333333;
    border-bottom: 1px solid #EDE0D0;
}}
.col-name  {{ font-weight: 500; }}
.col-price {{ font-variant-numeric: tabular-nums; }}
.col-change{{ font-variant-numeric: tabular-nums; }}
.col-trend {{ font-weight: 700; }}

/* ── Footer ─────────────────────────────────────── */
.card-bottom {{
    background: #FFF3E6;
    padding: 8px 14px;
    font-size: 11.5px;
    color: #8B6914;
    border-top: 1px solid #EDE0D0;
    line-height: 1.6;
}}
</style>
</head>
<body>
{variety_card}
{weight_card}
</body>
</html>"""


def generate_html(data: EggPriceData, for_screenshot: bool = False) -> str:
    variety_card = _render_variety_card(data)
    weight_card = _render_weight_card(data)
    body_bg = "#E8E0D8" if not for_screenshot else "#FFFFFF"
    return HTML_TEMPLATE.format(
        date=data.date,
        body_bg=body_bg,
        variety_card=variety_card,
        weight_card=weight_card,
    )


def generate_pngs(data: EggPriceData, output_dir: str = ".") -> list[str]:
    """用 Chrome headless 截图生成两张 PNG 卡片图片。"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    html = generate_html(data, for_screenshot=True)
    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", mode="w", encoding="utf-8", delete=False
    )
    tmp.write(html)
    tmp.flush()
    tmp_path = tmp.name
    tmp.close()

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--force-device-scale-factor=2")

    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(f"file:///{tmp_path.replace(os.sep, '/')}")
        driver.implicitly_wait(1)

        outputs = []
        for card_id, filename in [
            ("card-variety", "variety_card.png"),
            ("card-weight", "weight_card.png"),
        ]:
            el = driver.find_element("id", card_id)
            out_path = os.path.join(output_dir, filename)
            el.screenshot(out_path)
            outputs.append(out_path)
            print(f"已截图: {out_path}")
    finally:
        driver.quit()
        os.unlink(tmp_path)

    return outputs


def main():
    parser = argparse.ArgumentParser(description="鸡蛋价格卡片生成器")
    parser.add_argument("--input", "-i", help="JSON 数据文件路径")
    parser.add_argument("--format", "-f", choices=["html", "png"], default="html",
                        help="输出格式：html 或 png（默认 html）")
    parser.add_argument("--output", "-o", default=None, help="输出文件路径（仅 html 格式）")
    parser.add_argument("--output-dir", "-d", default=".", help="PNG 输出目录（仅 png 格式）")
    args = parser.parse_args()

    if args.input:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        print("未指定输入文件，使用示例数据。")
        raw = SAMPLE_DATA

    data = parse_json(raw)

    if args.format == "png":
        paths = generate_pngs(data, output_dir=args.output_dir)
        for p in paths:
            print(f"  -> {p}")
    else:
        out = args.output or "egg_price_cards.html"
        html = generate_html(data)
        Path(out).write_text(html, encoding="utf-8")
        print(f"卡片已生成: {out}")


if __name__ == "__main__":
    main()
