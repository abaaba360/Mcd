"""把 team_feed.py 的结构化结果渲染成一张可发群的图片卡片。

用法：
    python render_card.py out.json card.png
    python render_card.py out.json card.png --width 760 --scale 2

依赖：本机 Chrome（无头截图）+ Pillow（裁掉底部空白）。数字全部来自输入 json，
渲染层不生成任何业务数字——避免「图上的价和接口对不上」这类事故。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

BG = "#000000"        # 截图底色（仅用于裁边判定，不参与设计）
CARD = "#131316"


def find_browser():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    for n in ("chrome", "chromium", "msedge"):
        p = shutil.which(n)
        if p:
            return p
    raise SystemExit("找不到 Chrome/Edge，无法截图")


def constraint_text(person):
    bits = []
    if person.get("no_spicy"):
        bits.append("不吃辣")
    if person.get("meat"):
        bits.append(person["meat"])
    if person.get("max_kcal"):
        bits.append("减脂")
    if person.get("cold_drink"):
        bits.append("要冷饮")
    bits.append("≤¥%g" % person["budget"])
    return " · ".join(bits)


def build_html(data, width=760):
    store = data.get("store") or {}
    rows = data.get("rows") or []
    team = {p["name"]: p for p in data.get("team") or []}
    acc = data.get("accounts") or {}
    plan = data.get("coupon_plan") or []
    card = data.get("card") or {}

    coupon_save = round(sum(c.get("save") or 0 for c in plan), 2)
    no_card = acc.get("no_card")
    with_card = acc.get("with_card")
    final = round((with_card or 0) - coupon_save, 2) if with_card is not None else None
    saved = round((no_card or 0) - final, 2) if (no_card is not None and final is not None) else None

    # 券替换后的行：价格与名称都换掉，并标出来源
    by_who = {c["who"]: c for c in plan}

    trs = []
    for r in rows:
        who = r["who"]
        name, price = r["name"], r["price"]
        badge = '<span class="tag gold">需麦金卡</span>' if r.get("needs_card") else ""
        note = ""
        if who in by_who:
            c = by_who[who]
            name = c["to_name"]
            price = c["to_price"]
            badge = '<span class="tag red">用券</span>'
            note = '<div class="note">原选 %s ¥%.2f → 券后 −¥%.2f</div>' % (
                (c.get("from") or {}).get("name", ""),
                (c.get("from") or {}).get("price") or 0,
                c["save"],
            )
        kcal = ("%d kcal" % r["kcal"]) if r.get("kcal") else ""
        trs.append(
            """
      <div class="row">
        <div class="who">{who}</div>
        <div class="item">
          <div class="name">{name}{badge}</div>
          <div class="sub">{cons}{kc}</div>
          {note}
        </div>
        <div class="price">
          <div class="now">¥{price:.2f}</div>
          {was}
        </div>
      </div>""".format(
                who=who,
                name=name,
                badge=badge,
                cons=constraint_text(team.get(who, {})),
                kc=(" · " + kcal) if kcal else "",
                note=note,
                price=price or 0,
                was=(
                    # 57 不是「原价」，是「不买卡就得付这个价」——措辞必须准确
                    '<div class="was">不买卡 ¥%.2f</div>' % r["real_price"]
                    if r.get("needs_card") and r.get("real_price") and not (who in by_who)
                    else ""
                ),
            )
        )

    accounts = []
    if no_card is not None:
        accounts.append(('<div class="acc"><span>不买卡</span><b>¥%.2f</b></div>' % no_card))
    if with_card is not None:
        accounts.append(('<div class="acc"><span>买一张麦金卡</span><b>¥%.2f</b></div>' % with_card))
    if final is not None:
        accounts.append(
            '<div class="acc win"><span>再换券后龙焰</span><b>¥%.2f</b></div>' % final
        )

    meta_extra = ""
    if store.get("mealWindow"):
        bits = []
        if store.get("distance"):
            bits.append("距人民广场 %d 米" % store["distance"])
        bits.append(store["mealWindow"])
        meta_extra = "<br>" + " · ".join(bits)

    card_line = ""
    if card.get("productName") and card.get("price") is not None:
        tail = ""
        if saved:
            tail = " · 相对不买卡精确省 ¥%.2f" % saved
        card_line = '<div class="cardtip">以上含 %s ¥%.1f%s</div>' % (
            card["productName"],
            card["price"],
            tail,
        )

    # 右上角放最想让人看见的那个数：相对「不买卡」省了多少
    if saved:
        big = '<div class="l">一共省下</div><div class="n">¥%.0f</div>' % saved
    else:
        big = '<div class="l">人数</div><div class="n">%d</div>' % len(rows)

    return """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin:0; padding:0; background:{bg}; }}
  body {{ font-family: "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", sans-serif;
          -webkit-font-smoothing: antialiased; }}
  .wrap {{ width:{w}px; background:{card}; color:#f4f4f5; padding:0 0 30px; }}

  .head {{ background:#E4002B; padding:30px 34px 26px; position:relative; }}
  .head .kicker {{ font-size:14px; letter-spacing:.28em; color:#ffd9df; font-weight:600; }}
  .head h1 {{ margin:8px 0 0; font-size:38px; line-height:1.15; font-weight:800; letter-spacing:-.5px; }}
  .head .meta {{ margin-top:12px; font-size:15px; color:#ffe3e7; }}
  .head .meta b {{ color:#fff; font-weight:600; }}
  .head .big {{ position:absolute; right:30px; top:24px; text-align:right; }}
  .head .big .n {{ font-size:44px; font-weight:800; line-height:1.05; color:#FFC72C; letter-spacing:-1px; }}
  .head .big .l {{ font-size:12.5px; color:#ffd9df; letter-spacing:.14em; margin-bottom:4px; }}

  .rows {{ padding:8px 34px 0; }}
  .row {{ display:flex; align-items:center; gap:16px; padding:20px 0;
          border-bottom:1px solid rgba(255,255,255,.08); }}
  .row:last-child {{ border-bottom:none; }}
  .who {{ width:64px; flex:0 0 64px; font-size:20px; font-weight:700; color:#fff; }}
  .item {{ flex:1; min-width:0; }}
  .name {{ font-size:23px; font-weight:700; line-height:1.3; }}
  .sub {{ margin-top:6px; font-size:13.5px; color:#9a9aa2; }}
  .note {{ margin-top:5px; font-size:13px; color:#FFC72C; }}
  .price {{ text-align:right; flex:0 0 auto; }}
  .now {{ font-size:24px; font-weight:800; color:#fff; }}
  .was {{ font-size:12.5px; color:#8b8b93; margin-top:4px; }}
  .tag {{ display:inline-block; font-size:12px; font-weight:700; padding:3px 8px;
          border-radius:5px; margin-left:9px; vertical-align:3px; }}
  .tag.gold {{ background:rgba(255,199,44,.16); color:#FFC72C; }}
  .tag.red {{ background:rgba(228,0,43,.22); color:#ff7b8f; }}

  .accbox {{ margin:18px 34px 0; background:#1c1c21; border-radius:14px; padding:6px 18px; }}
  .acc {{ display:flex; align-items:center; padding:13px 0;
          border-bottom:1px solid rgba(255,255,255,.06); font-size:16px; }}
  .acc:last-child {{ border-bottom:none; }}
  .acc span {{ flex:1; color:#b9b9c2; }}
  .acc b {{ font-size:22px; color:#e9e9ec; font-weight:700; }}
  .acc i {{ font-style:normal; font-size:13px; color:#FFC72C; margin-left:12px; }}
  .acc.win span {{ color:#fff; font-weight:600; }}
  .acc.win b {{ color:#FFC72C; font-size:26px; }}
  .cardtip {{ margin:12px 34px 0; font-size:12.5px; color:#7f7f88; }}

  .copy {{ margin:20px 34px 0; border-left:3px solid #E4002B; padding:12px 0 12px 16px;
           font-size:14px; line-height:1.65; color:#c9c9d1; }}
  .foot {{ margin:18px 34px 0; font-size:11.5px; color:#6a6a72; line-height:1.7; }}
</style></head>
<body><div class="wrap">
  <div class="head">
    <div class="kicker">投 喂 清 单</div>
    <h1>今天午饭这么点</h1>
    <div class="meta"><b>{store}</b> · 到店自取堂食 · {n} 人{meta_extra}</div>
    <div class="big">{big}</div>
  </div>

  <div class="rows">{rows}</div>

  <div class="accbox">{accounts}</div>
  {cardline}

  <div class="copy">确认无误就回一句「下单」，我按这份清单建单，付款你自己点。</div>
  <div class="foot">价格来自麦当劳官方 MCP 的 calculate-price 实时返回，非估算。<br>
  券只能用在下单者本人账号上，同事自己的券在这里用不了。</div>
</div></body></html>""".format(
        bg=BG,
        card=CARD,
        w=width,
        store=store.get("storeName") or store.get("storeCode") or "",
        n=len(rows),
        big=big,
        meta_extra=meta_extra,
        rows="".join(trs),
        accounts="".join(accounts),
        cardline=card_line,
    )


def trim(path, bg=BG):
    """裁掉截图底部的纯底色区域。只在 Pillow 可用时执行。"""
    try:
        from PIL import Image
    except ImportError:
        return path
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    rgb = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
    bottom = h - 1
    while bottom > 0:
        if any(px[x, bottom] != rgb for x in range(0, w, 3)):
            break
        bottom -= 1
    if bottom < h - 1:
        im.crop((0, 0, w, min(h, bottom + 31))).save(path)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=760)
    ap.add_argument("--scale", type=int, default=2, help="像素倍率，2 = 视网膜清晰度")
    ap.add_argument("--height", type=int, default=2600, help="截图窗口高度上限")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)

    html = build_html(data, args.width)
    tmpdir = tempfile.mkdtemp(prefix="teamfeed_")
    html_path = os.path.join(tmpdir, "card.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    out_abs = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
    browser = find_browser()
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=%d" % args.scale,
        "--window-size=%d,%d" % (args.width, args.height),
        "--screenshot=%s" % out_abs,
        "file:///" + html_path.replace("\\", "/").lstrip("/"),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if not os.path.exists(out_abs):
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-800:])
        raise SystemExit("截图失败")

    trim(out_abs)
    size = os.path.getsize(out_abs)
    try:
        from PIL import Image
        with Image.open(out_abs) as im:
            print("已生成 %s（%d×%d，%.0f KB）" % (args.out, im.size[0], im.size[1], size / 1024))
    except ImportError:
        print("已生成 %s（%.0f KB）" % (args.out, size / 1024))


if __name__ == "__main__":
    main()
