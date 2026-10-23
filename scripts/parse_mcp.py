"""把 MCP 返回的文本解析成结构化数据，并生成菜单+营养的合并表。

MCP 的工具返回不是纯 JSON，而是「字段说明 + JSON」的混合文本，且格式并不统一：

    多数接口：  说明文本 … ## Original Response … {"success":true,...}
    券接口：    说明文本（含 Markdown 表格）… 直接跟 {"success":true,"data":[...]}
    营养接口：  {"data": "一张定宽字符串表格"}

所以不能只按 `## Original Response` 找，必须多点扫描。这个模块负责把这些都拆平。
"""

import json
import re

# 一条合法 MCP 响应的特征键
_RESP_KEYS = ("success", "code", "data", "message")


def extract_json(text):
    """从 MCP 返回文本里抠出响应 JSON。

    实测踩过的坑：`query-store-coupons` 的返回**没有** `## Original Response` 标记，
    只在结尾直接跟一段裸 JSON；而前面的字段说明里又有 `{data[].title}` 这种
    长得像 JSON 的占位符。所以：逐个 `{` 位置尝试解析，取第一个解得出且像响应体的。
    """
    if not isinstance(text, str):
        return None
    dec = json.JSONDecoder()

    starts = []
    marker = "## Original Response"
    if marker in text:
        b = text.find("{", text.find(marker))
        if b >= 0:
            starts.append(b)
    starts.extend(i for i, ch in enumerate(text) if ch == "{")

    seen = set()
    for pos in starts:
        if pos in seen:
            continue
        seen.add(pos)
        try:
            obj, _ = dec.raw_decode(text[pos:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and any(k in obj for k in _RESP_KEYS):
            return obj
    return None


def parse_nutrition(raw_string):
    """解析 list-nutrition-foods 的定宽表格。

    原始形态（首行是表头，实测 160 条数据）：
        "[160]{productName,nutritionDescription,energyKj,energyKcal,...}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
          ..."
    列序：名称, 描述, 能量kJ, 能量kcal, 蛋白质, 脂肪, 碳水, 钠, 钙
    返回 {餐品名: {kcal, protein, fat, carb, sodium, calcium}}
    """
    out = {}
    for line in raw_string.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or "productName" in line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 9:          # 列数严格对齐，宁缺勿错位
            continue
        name = parts[0]
        if not name or name == "null":
            continue

        def num(i):
            try:
                return float(parts[i])
            except ValueError:
                return None

        out[name] = {
            "energy_kj": num(2),
            "kcal": num(3),
            "protein": num(4),
            "fat": num(5),
            "carb": num(6),
            "sodium": num(7),
        }
    return out


def flatten_menu(payload):
    """把 query-meals 的 categories + meals 合并成扁平列表。

    关键：`discountType` 必须一起带出来。以「随单购」开头的价格是**需要买卡才成立**的，
    丢了这个字段，下游就会拿 ¥28 去比价，而实际结算是 ¥57。
    """
    data = payload.get("data") or {}
    meals = data.get("meals") or {}
    rows = []
    seen = set()
    for cat in data.get("categories") or []:
        for item in cat.get("meals") or []:
            code = item.get("code")
            if not code or code in seen:
                continue
            seen.add(code)
            detail = meals.get(code) or {}
            dtype = detail.get("discountType")
            rows.append(
                {
                    "code": code,
                    "name": detail.get("name") or code,
                    "price": _f(detail.get("currentPrice")),
                    "original_price": _f(detail.get("originalPrice")),
                    "category": re.sub(r"\s+", "", cat.get("name") or ""),
                    "tags": item.get("tags") or [],
                    "can_with_order": bool(detail.get("canWithOrder")),
                    "discount_type": dtype,
                    "needs_card": bool(dtype) and str(dtype).startswith("随单购"),
                    "with_order": detail.get("withOrder"),
                }
            )
    return rows


def parse_coupons(payload):
    """把 query-store-coupons 的 data 整理成列表。

    券带 `products[].productName`，这是**菜单里查不到的券专属商品的唯一名字来源**，
    必须保留，否则清单里会出现一栏「9900016523」。
    """
    rows = []
    for c in payload.get("data") or []:
        rows.append(
            {
                "title": c.get("title"),
                "couponId": c.get("couponId"),
                "couponCode": c.get("couponCode"),
                "valid": c.get("tradeDateTime"),
                "products": [
                    {"code": p.get("productCode"), "name": p.get("productName")}
                    for p in (c.get("products") or [])
                ],
            }
        )
    return rows


def match_nutrition(name, nut):
    """按名称对齐营养表。菜名带后缀（如 `_默认`）时退回包含匹配。"""
    if name in nut:
        return nut[name]
    base = str(name).split("_")[0]
    if base in nut:
        return nut[base]
    cands = [k for k in nut if k and (k in name or name in k)]
    if len(cands) == 1:
        return nut[cands[0]]
    return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
