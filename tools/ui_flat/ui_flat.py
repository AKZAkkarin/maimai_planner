#!/usr/bin/env python3
"""生成扁平简约的代码版 UI 素材（SVG）到 dataUI_VG。

不读取 dataUI 原图，文件名清单已硬编码；按分类和尺寸重新画：
- 默认头像：透明背景 + 问号 PNG
- 类型徽标：白底 + 彩色文字
- 难度按钮/底盘：纯色圆角块
- 排名/状态：圆角徽章 + 文字
- pageup：上箭头
- Number：按原来的 4×4 网格画数字
- ratingbg：纯色渐变条
- version：文字标牌

用法：python tools/ui_flat/ui_flat.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

PROJECT = Path(__file__).resolve().parents[2]
DST = PROJECT / "dataUI_VG"
BOLD_FONT = "Microsoft YaHei, Microsoft YaHei UI, sans-serif"
LIGHT_FONT = "Microsoft YaHei Light, Microsoft YaHei, sans-serif"
ASSET_NAMES = '''
Base_DUMMY.png
Base_DX.png
Base_STD.png
Base_UTG.png
Btn_ADV.png
Btn_BAS.png
Btn_EXP.png
Btn_MAS.png
Btn_REM.png
Btn_UTG.png
Mai_rating0.png
Mai_rating1000.png
Mai_rating10000.png
Mai_rating12000.png
Mai_rating13000.png
Mai_rating14000.png
Mai_rating14250.png
Mai_rating14500.png
Mai_rating14750.png
Mai_rating15000.png
Mai_rating15250.png
Mai_rating15500.png
Mai_rating15750.png
Mai_rating16000.png
Mai_rating16250.png
Mai_rating16500.png
Mai_rating16750.png
Mai_rating2000.png
Mai_rating4000.png
Mai_rating7000.png
Type_DX.png
Type_STD.png
Type_UTG.png
Type_UTG_2p.png
UI_CMN_TabTitle_MaimaiTitle_Ver100.png
UI_CMN_TabTitle_MaimaiTitle_Ver110.png
UI_CMN_TabTitle_MaimaiTitle_Ver120.png
UI_CMN_TabTitle_MaimaiTitle_Ver130.png
UI_CMN_TabTitle_MaimaiTitle_Ver140.png
UI_CMN_TabTitle_MaimaiTitle_Ver150.png
UI_CMN_TabTitle_MaimaiTitle_Ver160.png
UI_CMN_TabTitle_MaimaiTitle_Ver170.png
UI_CMN_TabTitle_MaimaiTitle_Ver180.png
UI_CMN_TabTitle_MaimaiTitle_Ver185.png
UI_CMN_TabTitle_MaimaiTitle_Ver190.png
UI_CMN_TabTitle_MaimaiTitle_Ver195.png
UI_CMN_TabTitle_MaimaiTitle_Ver199.png
UI_CMN_TabTitle_MaimaiTitle_Ver200.png
UI_CMN_TabTitle_MaimaiTitle_Ver210.png
UI_CMN_TabTitle_MaimaiTitle_Ver214.png
UI_CMN_TabTitle_MaimaiTitle_Ver215.png
UI_CMN_TabTitle_MaimaiTitle_Ver220.png
UI_CMN_TabTitle_MaimaiTitle_Ver225.png
UI_CMN_TabTitle_MaimaiTitle_Ver230.png
UI_CMN_TabTitle_MaimaiTitle_Ver235.png
UI_CMN_TabTitle_MaimaiTitle_Ver240.png
UI_CMN_TabTitle_MaimaiTitle_Ver245.png
UI_CMN_TabTitle_MaimaiTitle_Ver250.png
UI_CMN_TabTitle_MaimaiTitle_Ver255.png
UI_CMN_TabTitle_MaimaiTitle_Ver260.png
UI_CMN_TabTitle_MaimaiTitle_Ver265.png
UI_CMN_TabTitle_MaimaiTitle_Ver270.png
UI_GAM_Rank_A.png
UI_GAM_Rank_AA.png
UI_GAM_Rank_AAA.png
UI_GAM_Rank_B.png
UI_GAM_Rank_BB.png
UI_GAM_Rank_BBB.png
UI_GAM_Rank_C.png
UI_GAM_Rank_D.png
UI_GAM_Rank_S.png
UI_GAM_Rank_SS.png
UI_GAM_Rank_SSS.png
UI_GAM_Rank_SSSp.png
UI_GAM_Rank_SSp.png
UI_GAM_Rank_Sp.png
UI_MSS_MBase_Icon_AP.png
UI_MSS_MBase_Icon_APp.png
UI_MSS_MBase_Icon_FC.png
UI_MSS_MBase_Icon_FCp.png
UI_MSS_MBase_Icon_FS.png
UI_MSS_MBase_Icon_FSD.png
UI_MSS_MBase_Icon_FSDp.png
UI_MSS_MBase_Icon_FSp.png
UI_MSS_MBase_Icon_SP.png
UI_MSS_Rival_DXscoreIcon_01.png
UI_MSS_Rival_DXscoreIcon_02.png
UI_MSS_Rival_DXscoreIcon_03.png
UI_MSS_Rival_DXscoreIcon_04.png
UI_MSS_Rival_DXscoreIcon_05.png
UI_NUM_ADV.png
UI_NUM_BAS.png
UI_NUM_Dxrating.png
UI_NUM_EXP.png
UI_NUM_MAS.png
UI_NUM_REM.png
UI_NUM_UTG.png
UI_NUM_UTG_Question.png
UI_Num_A.png
UI_Num_S.png
UI_Num_Score_De.png
UI_RSL_AP_Text_01.png
UI_RSL_APp_Text_01.png
UI_RSL_FC_Text_01.png
UI_RSL_FCp_Text_01.png
UI_RSL_FSD_Text_01.png
UI_RSL_FSDp_Text_01.png
dummyHeadPicture.png
pageup.png
'''.strip().splitlines()
TZ = timezone(timedelta(hours=8))

DIFF = {
    "BAS": ("#64d86b", "#0d2411"),
    "ADV": ("#f2c94c", "#2a2208"),
    "EXP": ("#e8656b", "#2a0c0d"),
    "MAS": ("#a97ae6", "#1e102b"),
    "REM": ("#c9a0ff", "#21152d"),
    "UTG": ("#ff86c8", "#2b0d20"),
}
RANK = {
    "D": "#8b93a5", "C": "#65c98a", "B": "#5aa9ff", "BB": "#5aa9ff", "BBB": "#5aa9ff",
    "A": "#ff8fc7", "AA": "#ff8fc7", "AAA": "#ff8fc7",
    "S": "#f6c945", "Sp": "#f6c945", "SS": "#f6c945", "SSp": "#f6c945",
    "SSS": "#ffd86b", "SSSp": "#ffe8a3",
}
TYPE = {"DX": "#e87524", "STD": "#2f80ed", "UTG": "#2f80ed"}
STATUS = {"FC": "#7aa2ff", "FCp": "#7aa2ff", "AP": "#f6c945", "APp": "#f6c945",
          "FS": "#65c98a", "FSp": "#65c98a", "FSD": "#b18cff", "FSDp": "#b18cff",
          "SP": "#ff8fc7"}
VERSION_NAME = {
    "100": "maimai", "110": "maimai PLUS", "120": "GreeN", "130": "GreeN PLUS",
    "140": "ORANGE", "150": "ORANGE PLUS", "160": "PiNK", "170": "PiNK PLUS",
    "180": "MURASAKi", "185": "MURASAKi PLUS", "190": "MiLK", "195": "MiLK PLUS",
    "199": "FINAL", "200": "DX", "210": "DX PLUS", "214": "Splash", "215": "Splash PLUS",
    "220": "UNIVERSE", "225": "UNIVERSE PLUS", "230": "FESTiVAL", "235": "FESTiVAL PLUS",
    "240": "BUDDiES", "245": "BUDDiES PLUS", "250": "PRiSM", "255": "PRiSM PLUS",
    "260": "CiRCLE", "265": "CiRCLE PLUS", "270": "MAGiCAL",
}


def svg(w: int, h: int, body: str, title: str = "") -> str:
    t = f"<title>{escape(title)}</title>" if title else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}">{t}{body}</svg>\n')


def text(x: float, y: float, value: str, size: int, fill: str = "#fff",
         weight: int = 700, anchor: str = "middle", family: str = "") -> str:
    if not family:
        if weight >= 600:
            family, weight = BOLD_FONT, 700
        else:
            family, weight = LIGHT_FONT, 300
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
            f'font-family="{family}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}">{escape(value)}</text>')


def flat_digit_sheet(w: int, h: int, chars: str, color: str = "#ffffff") -> str:
    cols, rows = 4, 4
    cw, ch = w / cols, h / rows
    body = []
    for i, char in enumerate(chars[:16]):
        col, row = i % cols, i // cols
        ox, oy = col * cw, row * ch
        cx = ox + cw / 2
        if i == 15:
            continue
        if i == 10:  # +
            body.append(f'<path d="M{ox+9},{oy+15} H{ox+36} M{ox+22.5},{oy+2} V{oy+28}" '
                        'stroke="#fff" stroke-width="6" stroke-linecap="round"/>')
        elif i == 11:  # -
            body.append(f'<path d="M{ox+12},{oy+27} H{ox+35}" stroke="#fff" '
                        'stroke-width="6" stroke-linecap="round"/>')
        elif i == 12:  # ,
            body.append(f'<path d="M{ox+23},{oy+34} L{ox+18},{oy+50}" stroke="#fff" '
                        'stroke-width="5" stroke-linecap="round"/>')
        elif i == 13:  # .
            body.append(f'<circle cx="{ox+24}" cy="{oy+39}" r="6" fill="#fff"/>')
        elif i == 14:  # LV
            body.append(text(cx, oy + ch * 0.87, "LV", int(ch * 0.48), color, 900,
                             "middle", BOLD_FONT))
        else:
            body.append(text(cx, oy + ch * 0.93, char, int(ch * 0.98), color, 900,
                             "middle", BOLD_FONT))
    return "".join(body)


def draw_rank(name: str) -> str:
    grade = name.replace("UI_GAM_Rank_", "").replace(".png", "")
    label = {"Sp": "S+", "SSp": "SS+", "SSSp": "SSS+"}.get(grade, grade)
    color = RANK.get(grade, "#f6c945")
    body = ('<rect x="2" y="2" width="116" height="44" rx="14" fill="#10141c" '
            f'stroke="{color}" stroke-width="3"/>'
            + text(60, 33, label, 26, color, 900))
    return svg(120, 48, body, label)


def draw_status(name: str) -> str:
    code = name.replace("UI_MSS_MBase_Icon_", "").replace(".png", "")
    plus = code.endswith("p")
    base = code[:-1] if plus else code
    label = {"FDX": "FDX", "SP": "SP"}.get(base, base)
    color = STATUS.get(code, STATUS.get(base, "#7aa2ff"))
    body = (f'<circle cx="40" cy="40" r="34" fill="#111722" stroke="{color}" stroke-width="4"/>'
            + text(40, 51, label, 30 if len(label) <= 2 else 23, color, 900))
    if plus:
        body += (f'<text x="64" y="31" text-anchor="middle" font-family="{BOLD_FONT}" '
                 f'font-size="30" font-weight="700" fill="#ffffff" stroke="{color}" '
                 f'stroke-width="4" paint-order="stroke">+</text>')
    return svg(80, 80, body, label + ("+" if plus else ""))


def draw_status_text(name: str) -> str:
    code = name.replace("UI_RSL_", "").split("_")[0].replace(".png", "")
    label = {"FCp": "FC+", "APp": "AP+", "FSDp": "FDX+"}.get(code, code)
    color = STATUS.get(code, "#7aa2ff")
    body = (f'<rect x="2" y="2" width="120" height="60" rx="16" fill="#111722" '
            f'stroke="{color}" stroke-width="3"/>' + text(62, 43, label, 30, color, 900))
    return svg(124, 64, body, label)


def draw_type(name: str) -> str:
    if name == "Type_UTG_2p.png":
        body = ('<rect x="2" y="2" width="132" height="48" rx="10" fill="#f1c75b" '
                'stroke="#fff1b0" stroke-width="2"/>'
                '<rect x="9" y="9" width="118" height="34" rx="6" fill="#d83b4a"/>'
                + text(68, 36, "2P", 25, "#fff", 900))
        return svg(136, 52, body, "UTG 2P")
    code = name.replace("Type_", "").replace(".png", "")
    if code == "STD":
        body = ('<rect x="2" y="2" width="80" height="24" rx="6" fill="#45aeff" '
                'stroke="#dff3ff" stroke-width="2"/>' + text(42, 20, "标准", 16, "#ffffff", 900))
        return svg(84, 28, body, code)
    if code == "UTG":
        body = ('<rect x="2" y="2" width="80" height="24" rx="6" fill="#0f2852" '
                'stroke="#ff9ff5" stroke-width="2"/>')
        return svg(84, 28, body, code)
    color = "#e87524" if code == "DX" else TYPE.get(code, "#2f80ed")
    body = (f'<rect x="2" y="2" width="80" height="24" rx="6" fill="#f7f8fb" '
            f'stroke="{color}" stroke-width="2"/>' + text(42, 20, code, 17, color, 900))
    return svg(84, 28, body, code)


def draw_button(name: str) -> str:
    code = name.replace("Btn_", "").replace(".png", "")
    bg = {
        "BAS": "#95f461", "ADV": "#fbe72e", "EXP": "#fe5240",
        "MAS": "#c77efd", "REM": "#c18fee", "UTG": "#ed7cff",
    }.get(code, "#555555")
    # 药丸/按钮只保留纯色和白色轮廓，不再写深色文字，避免 border-image 拉伸后出现黑块。
    # 原图可见形状约 64×52，圆角约 16，不是整圆。
    body = (f'<rect x="8" y="14" width="64" height="52" rx="16" fill="{bg}" '
            f'stroke="#ffffff" stroke-width="3"/>')
    return svg(80, 80, body, code)


def draw_base(name: str) -> str:
    code = name.replace("Base_", "").replace(".png", "")
    # (底板, 描边, 标题底板)
    palette = {
        "STD": ("#abb8c4", "#1d2329", "#3a4652"),
        "DX": ("#84bdea", "#0b263c", "#154c78"),
        "UTG": ("#ff6ffd", "#ed29af", "#0e3d7e"),
        "DUMMY": ("#b7b7b7", "#505050", "#464646"),
    }
    bg, border, title = palette.get(code, palette["DUMMY"])
    # 按 BasePsdlayer 的六个可见层重画：底板、描边、封面黑框、标题底板、分界线、作者白底。
    body = (f'<rect x="20" y="56" width="244" height="335" rx="14" fill="{bg}" '
            f'stroke="{border}" stroke-width="2"/>'
            f'<circle cx="142" cy="170" r="118" fill="none" stroke="{border}" stroke-width="10" opacity=".14"/>'
            f'<circle cx="142" cy="170" r="92" fill="none" stroke="{border}" stroke-width="8" opacity=".12"/>'
            '<rect x="42" y="74" width="200" height="200" fill="#000"/>'
            f'<rect x="22" y="303" width="240" height="41" rx="3" fill="{title}"/>'
            f'<rect x="21" y="349" width="242" height="2" fill="{border}"/>'
            # 作者白底与底板同形状，只是上缘在分界线处被截平。
            '<path d="M21 351 H263 V375 Q263 389 249 389 H35 Q21 389 21 375 Z" fill="#ffffff"/>')
    return svg(284, 412, body, code)


def draw_rank_or_status(name: str) -> str:
    if name.startswith("UI_GAM_Rank_"):
        return draw_rank(name)
    if name.startswith("UI_MSS_MBase_Icon_"):
        return draw_status(name)
    return draw_status_text(name)


def draw_dx_icon(name: str) -> str:
    n = name.replace("UI_MSS_Rival_DXscoreIcon_0", "").replace(".png", "")
    color = "#4fd15f" if n in ("1", "2") else ("#f28a2e" if n in ("3", "4") else "#ffd74a")
    # 原图是“闪光 + 数字”的小药丸；扁平版保留这个信息层级。
    body = (f'<rect x="1" y="1" width="38" height="22" rx="11" fill="{color}"/>'
            '<path d="M10 5 L12.2 9.8 L17 12 L12.2 14.2 L10 19 L7.8 14.2 L3 12 L7.8 9.8 Z" fill="#fff"/>'
            + text(28, 17, n, 13, "#fff", 900))
    return svg(40, 24, body, f"DX {n}")


def draw_pageup() -> str:
    body = ('<rect x="8" y="8" width="159" height="174" rx="28" fill="#171b24" '
            'stroke="#7aa2ff" stroke-width="5"/>'
            '<path d="M88 48 L132 104 H112 V146 H64 V104 H44 Z" fill="#7aa2ff"/>')
    return svg(175, 190, body, "回到顶端")


def draw_dummy() -> str:
    body = ('<circle cx="94" cy="118" r="86" fill="#7aa2ff"/>'
            '<circle cx="162" cy="118" r="86" fill="#ff86c8" fill-opacity=".9"/>'
            + text(128, 160, "?", 150, "#fff", 900))
    return svg(256, 256, body, "默认头像")


def draw_rating(name: str) -> str:
    n = name.replace("Mai_rating", "").replace(".png", "")
    try:
        tier = int(n)
    except ValueError:
        tier = 0
    colors = ["#2f80ed", "#49b5c9", "#64d86b", "#f2c94c", "#f0a038", "#e8656b",
              "#b18cff", "#ff86c8", "#f6c945", "#fff1b0"]
    c = colors[min(len(colors) - 1, max(0, tier // 2000))]
    body = (f'<defs><linearGradient id="g" x1="0" x2="1"><stop stop-color="#151a24"/>'
            f'<stop offset=".55" stop-color="{c}" stop-opacity=".38"/>'
            f'<stop offset="1" stop-color="{c}"/></linearGradient></defs>'
            '<rect width="664" height="130" rx="16" fill="url(#g)"/>'
            f'<path d="M0 104 L664 70 V130 H0 Z" fill="{c}" fill-opacity=".18"/>')
    return svg(664, 130, body, f"rating {tier}")


def draw_version(name: str) -> str:
    code = name.replace("UI_CMN_TabTitle_MaimaiTitle_Ver", "").replace(".png", "")
    label = VERSION_NAME.get(code, "Ver " + code)
    body = ('<rect x="3" y="3" width="326" height="154" rx="22" fill="#171b24" '
            'stroke="#7aa2ff" stroke-width="4"/>'
            + text(166, 78, "maimai", 42, "#f4f7ff", 900)
            + text(166, 124, label, 28, "#7aa2ff", 800))
    return svg(332, 160, body, label)


def draw_number_sheet(name: str) -> str:
    if name == "UI_NUM_UTG_Question.png":
        return svg(48, 60, text(24, 54, "?", 52, "#fff", 900), "?")
    if name == "UI_NUM_Dxrating.png":
        return svg(120, 136, flat_digit_sheet(120, 136, "0123456789", "#f6c945"), "dx rating digits")
    if name in ("UI_Num_A.png", "UI_Num_S.png", "UI_Num_Score_De.png"):
        return svg(344, 472, flat_digit_sheet(344, 472, "0123456789", "#fff"), name)
    return svg(192, 240, flat_digit_sheet(192, 240, "0123456789+-,.LV", "#fff"), name)


def render(rel: Path) -> str:
    name = rel.name
    if name == "dummyHeadPicture.png":
        return draw_dummy()
    if name == "pageup.png":
        return draw_pageup()
    if name.startswith("Type_"):
        return draw_type(name)
    if name.startswith("Btn_"):
        return draw_button(name)
    if name.startswith("Base_"):
        return draw_base(name)
    if name.startswith("UI_GAM_Rank_") or name.startswith("UI_MSS_MBase_Icon_") or name.startswith("UI_RSL_"):
        return draw_rank_or_status(name)
    if name.startswith("UI_MSS_Rival_DXscoreIcon_"):
        return draw_dx_icon(name)
    if name.startswith("Mai_rating"):
        return draw_rating(name)
    if name.startswith("UI_CMN_TabTitle_MaimaiTitle_"):
        return draw_version(name)
    if name.startswith("UI_NUM_") or name.startswith("UI_Num_"):
        return draw_number_sheet(name)
    return svg(64, 64, '<rect width="64" height="64" rx="12" fill="#232a38"/>', name)


def asset_rel(name: str) -> Path:
    if name == "dummyHeadPicture.png":
        return Path("dummyHeadPicture") / name
    if name.startswith("UI_NUM_") or name.startswith("UI_Num_"):
        return Path("Number") / name
    if name.startswith("UI_GAM_Rank_"):
        return Path("rank") / name
    if name.startswith("Mai_rating"):
        return Path("ratingbg") / name
    if name.startswith("UI_MSS_MBase_Icon_") or name.startswith("UI_RSL_"):
        return Path("status") / name
    if name.startswith("UI_MSS_Rival_DXscoreIcon_"):
        return Path("dxScoreSmall") / name
    if name.startswith("UI_CMN_TabTitle_MaimaiTitle_"):
        return Path("version") / name
    if name.startswith(("Base_", "Btn_", "Type_")) or name == "pageup.png":
        return Path("musicLib") / name
    raise ValueError("unknown UI asset: " + name)


def expected_outputs() -> dict[str, Path]:
    out = {}
    for name in ASSET_NAMES:
        rel = asset_rel(name)
        out[name] = rel if name == "dummyHeadPicture.png" else rel.with_suffix(".svg")
    return out


def is_complete() -> bool:
    manifest = DST / "manifest.json"
    if not manifest.is_file():
        return False
    for name, rel in expected_outputs().items():
        if not (DST / rel).is_file():
            return False
    return True


def write_dummy_png(out: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    out.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = None
    for font_path in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"):
        try:
            font = ImageFont.truetype(font_path, 164)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), "?", font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(((256 - tw) / 2 - box[0], (256 - th) / 2 - box[1]), "?",
              fill=(122, 162, 255, 255), font=font)
    image.save(out)


def generate_all() -> tuple[bool, str]:
    try:
        DST.mkdir(parents=True, exist_ok=True)
        manifest = []
        for name in ASSET_NAMES:
            rel = asset_rel(name)
            if name == "dummyHeadPicture.png":
                out = DST / rel
                write_dummy_png(out)
                # 清掉旧版本可能留下的 SVG，服务端只认这张 PNG。
                (DST / rel.with_suffix(".svg")).unlink(missing_ok=True)
            else:
                out = DST / rel.with_suffix(".svg")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(render(rel), encoding="utf-8")
            manifest.append({"from": rel.as_posix(),
                             "to": out.relative_to(DST).as_posix(),
                             "bytes": out.stat().st_size})
        doc = {"schema": 1,
               "generatedAt": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+0800"),
               "source": "ui_flat.py",
               "files": manifest}
        (DST / "manifest.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, f"生成 {len(manifest)} 个素材"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def ensure_assets() -> tuple[bool, str]:
    if is_complete():
        return True, "素材完整，跳过生成"
    return generate_all()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="无条件重建全部素材")
    args = ap.parse_args(argv)
    ok, msg = generate_all() if args.force else ensure_assets()
    print(msg)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
