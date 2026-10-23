"""投喂官参考实现：约束求解 + 双账对比 + 券替换建议。

用法：
    python team_feed.py --demo                     # 内置 4 人示例
    python team_feed.py --team t.json              # 自定义团队
    python team_feed.py --demo --coupons c.json    # 叠加券替换建议
    python team_feed.py --demo --json out.json     # 顺带输出结构化结果（供渲染用）

数据：默认读同目录 menu.json（2026-10-23 真实快照，离线可跑）。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---------------------------------------------------------------- 约束词典
SPICY = ["麦辣", "香辣", "辣椒", "龙焰", "辣味", "韩式辣椒"]

# 注意：不能用裸「牛」——「纯牛奶」里也有「牛」，实测会把人配成一份牛奶。
BEEF = ["牛肉", "安格斯", "巨无霸", "牛堡"]
CHICKEN = ["鸡"]
FISH = ["鱼"]
VEG_ONLY = ["薯条", "玉米", "沙拉", "苹果", "冰淇淋", "派", "饮料", "麦咖啡", "拿铁", "美式"]

MEAT_RULES = {
    "牛肉": BEEF,
    "鸡肉": CHICKEN,
    "鱼": FISH,
    "素食": VEG_ONLY,
}

# 名字里出现这些词就不是「一顿饭」：小食、饮料、加购品。
# 踩过的坑：不设这道闸，求解器会给一个人配一份 ¥3 的蘸酱当午餐。
NOT_MAIN_WORDS = [
    "薯饼", "薯条", "鸡块", "鸡翅", "蘸酱", "冰淇淋", "新地", "派",
    "奶", "咖啡", "拿铁", "美式", "可乐", "雪碧", "纯悦", "冰露",
    "水", "茶", "果汁", "橙", "酱", "丝巾", "玩具", "杯",
]
MAIN_WORDS = ["堡", "卷", "套餐", "三件套", "四件套", "盒", "饭"]


def is_main(meal):
    """判断能不能算「一份正餐」。按名字判定，不要凭分类名想当然。"""
    name = meal.get("name") or ""
    if any(w in name for w in NOT_MAIN_WORDS):
        return False
    return any(w in name for w in MAIN_WORDS)


def violates(meal, person):
    """返回 None 表示通过，否则返回不满足的原因。

    注意「要不要冷饮」不在这里——它是偏好不是硬约束，放进 score() 加分。
    当成硬过滤会把没有「冷饮」标签的套餐全砍掉。
    """
    name = meal["name"]
    if not is_main(meal):
        return "不是正餐"
    if person.get("no_spicy") and any(k in name for k in SPICY):
        return "含辣"
    meat = person.get("meat")
    if meat and not any(k in name for k in MEAT_RULES.get(meat, [])):
        return "非%s" % meat
    price = meal.get("price")
    if price is None or price > person["budget"]:
        return "超预算"
    kcal = meal.get("kcal")
    if person.get("max_kcal"):
        if kcal is None:
            return "热量未知"
        if kcal > person["max_kcal"]:
            return "热量%d>%d" % (kcal, person["max_kcal"])
    return None


def score(meal, person):
    """排序分：越贴合目标越高。"""
    s = 0.0
    price = meal.get("price") or 0
    s -= price * 0.1                                       # 便宜更好，权重最低
    if person.get("max_kcal") and meal.get("kcal") is not None:
        s -= abs(meal["kcal"] - person["max_kcal"] * 0.8) / 100.0
    if meal.get("original_price") and price:
        s += (meal["original_price"] - price) * 0.05       # 本身在打折的优先
    name = meal.get("name") or ""
    if "套餐" in name or "件套" in name:
        s += 2.0                                           # 一整份套餐优于单点
    if person.get("cold_drink") and ("件套" in name or "套餐" in name):
        s += 3.0                                           # 要冷饮的人优先给带饮料的
    return s


def solve(team, meals):
    picked, used = {}, set()
    for p in team:
        cands = []
        for m in meals:
            if m["code"] in used:
                continue
            if violates(m, p) is None:
                cands.append((score(m, p), m))
        cands.sort(key=lambda x: -x[0])
        if not cands:
            picked[p["name"]] = None
        else:
            best = cands[0][1]
            used.add(best["code"])
            picked[p["name"]] = {"meal": best, "alternatives": [c[1] for c in cands[1:4]]}
    return picked


def assign_coupons(picked, coupons, team):
    """券替换建议。

    券的实际省额只有一个可信来源：`calculate-price` 的返回。所以 `coupons.json` 里
    必须显式带 `coupon_price`（= 带 couponId 算价后该商品的实际单价），
    没有这个字段的券**一律跳过**，不能从券标题里猜——标题通常不含价格。

    券返回的 `products[].productName` 是券专属商品的唯一名字来源，
    这类商品（如「龙焰鸡腿堡两件套」）在 query-meals 菜单里根本不存在。

    替换前会用**原主人的约束**复核一遍：券商品也要满足忌口/预算/热量。
    热量未知的人（设了 max_kcal）不会被推荐——宁可少省几块，也不给出无法核实的建议。
    """
    people = {p["name"]: p for p in team}
    plan = []
    taken = set()
    for c in coupons:
        if c.get("coupon_price") is None:
            continue
        prod = c.get("productCode")
        price = float(c["coupon_price"])
        name = c.get("productName") or prod
        best = None
        for who, v in picked.items():
            if not v or who in taken or who not in people:
                continue
            cur = v["meal"]
            if cur.get("code") == prod:            # 已经是这个商品，无需替换
                continue
            probe = {"code": prod, "name": name, "price": price, "kcal": c.get("kcal")}
            if violates(probe, people[who]) is not None:
                continue                           # 不满足该人约束，不用
            save = (cur.get("price") or 0) - price
            if save > 0 and (best is None or save > best[0]):
                best = (save, who, cur)
        if best:
            taken.add(best[1])
            plan.append(
                {
                    "who": best[1],
                    "from": best[2],
                    "to_code": prod,
                    "to_name": name,
                    "to_price": price,
                    "coupon": c.get("title"),
                    "save": round(best[0], 2),
                }
            )
    return plan


DEMO_TEAM = [
    {"name": "阿伟", "budget": 35, "no_spicy": True},
    {"name": "小鹿", "budget": 30, "max_kcal": 600},
    {"name": "老陈", "budget": 40, "meat": "牛肉"},
    {"name": "阿May", "budget": 25, "cold_drink": True},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--team", help="团队约束 json")
    ap.add_argument("--coupons", help="券清单 json（需含 coupon_price）")
    ap.add_argument("--menu", default=os.path.join(HERE, "menu.json"))
    ap.add_argument("--json", dest="out", help="结果另存为 json，供卡片渲染使用")
    args = ap.parse_args()

    team = DEMO_TEAM
    if args.team:
        with open(args.team, encoding="utf-8") as f:
            team = json.load(f)

    with open(args.menu, encoding="utf-8") as f:
        snapshot = json.load(f)
    store, meals = snapshot["store"], snapshot["meals"]
    card = snapshot.get("card") or {"price": 9.9, "productName": "麦金卡体验卡（15天）"}

    picked = solve(team, meals)

    # 两档账：不买卡（需卡品按原价）/ 买卡（全部按现价 + 一张卡钱）
    no_card = card_yes = 0.0
    need_card = []
    for p in team:
        v = picked.get(p["name"])
        if not v:
            continue
        m = v["meal"]
        if m.get("needs_card"):
            need_card.append(m)
            no_card += m.get("original_price") or m["price"] or 0
        else:
            no_card += m["price"] or 0
        card_yes += m["price"] or 0
    if need_card:
        card_yes += card["price"]

    coupon_plan = []
    if args.coupons:
        with open(args.coupons, encoding="utf-8") as f:
            coupon_plan = assign_coupons(picked, json.load(f), team)

    print("门店：%s（%s）" % (store["storeName"], store["storeCode"]))
    print(
        "在售 %d 项 · 有正热量 %d 项 · 需随单购麦金卡才降价 %d 项"
        % (
            len(meals),
            sum(1 for m in meals if m.get("kcal")),
            sum(1 for m in meals if m.get("needs_card")),
        )
    )
    print()

    print("| 谁 | 点什么 | 约束核对 | 标价 | 不买卡实付 |")
    print("|---|---|---|---|---|")
    rows = []
    for p in team:
        v = picked.get(p["name"])
        if not v:
            print("| %s | **无满足条件的餐品** | 需放宽约束 | — | — |" % p["name"])
            continue
        m = v["meal"]
        if m.get("kcal"):
            kcal = "%d kcal" % m["kcal"]
        else:
            # 套餐热量要拆子项才能算，这里不假装知道；没设限的人也不需要
            kcal = "未拆" if p.get("max_kcal") else "—"
        real = (m.get("original_price") or m["price"]) if m.get("needs_card") else m["price"]
        flag = " ⚠需卡" if m.get("needs_card") else ""
        print(
            "| %s | %s%s | %s | ¥%.1f | ¥%.1f |"
            % (p["name"], m["name"], flag, kcal, m["price"] or 0, real)
        )
        rows.append(
            {
                "who": p["name"],
                "code": m["code"],
                "name": m["name"],
                "price": m["price"],
                "real_price": real,
                "needs_card": bool(m.get("needs_card")),
                "kcal": m.get("kcal"),
            }
        )

    print()
    print("不买卡：¥%.2f" % no_card)
    if need_card:
        verdict = (
            "买卡更省 ¥%.2f" % (no_card - card_yes)
            if card_yes < no_card
            else "不买卡更省 ¥%.2f" % (card_yes - no_card)
        )
        print("买卡  ：¥%.2f（含卡 ¥%.1f）  → %s" % (card_yes, card["price"], verdict))
    print("⚠ 标价列是菜单价；带 ⚠需卡 的按原价结算才是真实成本。")

    if coupon_plan:
        print()
        print("券替换建议（券后价来自 calculate-price 实测）：")
        for c in coupon_plan:
            print(
                "  %s：%s → %s ¥%.2f，省 ¥%.2f（%s）"
                % (c["who"], c["from"]["name"], c["to_name"], c["to_price"], c["save"], c["coupon"])
            )

    if args.out:
        result = {
            "store": store,
            "card": card,
            "team": team,
            "rows": rows,
            "accounts": {"no_card": round(no_card, 2), "with_card": round(card_yes, 2)},
            # 保留 from：卡片要显示「原选什么 → 换成什么」，否则读者看不出省在哪
            "coupon_plan": [
                {
                    "who": c["who"],
                    "from": {"name": c["from"]["name"], "price": c["from"]["price"]},
                    "to_name": c["to_name"],
                    "to_price": c["to_price"],
                    "coupon": c["coupon"],
                    "save": c["save"],
                }
                for c in coupon_plan
            ],
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print("\n结构化结果已写入 %s" % args.out)


if __name__ == "__main__":
    main()
