# MCP 集成说明

本文档记录 `team-feed` 实际接入的麦当劳 MCP Server、调用的 Tool、真实调用流程与业务价值。
**所有参数与返回数字均来自 2026-10-23 的真实调用，不是照文档推演的。**

---

## 1. MCP Server

| 项 | 值 |
|---|---|
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| Token 申请 | [open.mcd.cn/mcp](https://open.mcd.cn/mcp) 手机号登录 → 控制台 → 激活 |
| 限流 | 600 次/分钟 |
| 错误码 | `401` Token 无效 · `429` 触发限流 |

配置示例见 [`mcp-config.example.json`](mcp-config.example.json)，Token 只以环境变量占位符出现。

### 一个必须先处理的问题：返回不是纯 JSON

MCP 的返回体是「字段说明 + Markdown 表格 + `## Original Response` + JSON」的混合文本，且不同 Tool 的格式并不统一——`query-store-coupons` 就**没有** `## Original Response` 标记，末尾直接跟裸 JSON，正文里还夹着 `{data[].title}` 这类看起来像 JSON 的占位符。

`scripts/parse_mcp.py` 的处理方式是：从每个 `{` 位置尝试 `raw_decode`，取第一个同时含有 `success`/`code`/`data` 字段的对象。只用标记匹配会在券接口上直接失败。

### 线上工具数与文档不一致

`tools/list` 实际返回 **35 个 Tool**，而公开文档只列了 33 个：

- 文档未收录：`query-promotions`、`query-survey-coupon`
- 文档写作 `query-partystore-date`，线上实际是 `query-party-store-date`

---

## 2. 实际调用的 Tool

| # | Tool | 用途 | 关键参数 | 实测要点 |
|---|---|---|---|---|
| 1 | `now-time-info` | 取服务器时间，判断当前餐段 | 无 | 决定哪些菜品当前在售 |
| 2 | `query-nearby-stores` | 定位门店 | `searchType: 2`、`city`、`keyword`、`beType: 1` | **线上 schema 没有经纬度参数**，`searchType` 只有 1（收藏）和 2（城市+关键词），没有 3 |
| 3 | `query-meals` | 拉门店全量菜单 | `storeCode`、`beType: 1`、`orderType: 1` | 返回 122 项在售，含 `discountType` 与 `withOrder` |
| 4 | `query-meal-detail` | 拆套餐子项 | **`code`（不是 `productCode`）**、`storeCode` | 参数名传错直接 400 |
| 5 | `list-nutrition-foods` | 营养数据 | 无 | `data` 是**定宽字符串表格**，不是 JSON 数组 |
| 6 | `query-store-coupons` | 查门店可用券 | `storeCode`、`beType: 1`、`orderType: 1` | 券绑定 `products[].productCode` |
| 7 | `query-my-coupons` | 查本人卡包 | 无 | 返回账号已领的券 |
| 8 | `query-my-account` | 会员身份/积分 | 无 | 确认 Token 对应的账号 |
| 9 | `calculate-price` | 算价（核心） | `storeCode`、`beType`、`orderType`、`items[]`、`withOrder` | 金额单位为**分**；支持逐项带 `couponId`/`couponCode` |
| 10 | `available-coupons` | 可领券 | 无 | 用于判断是否有未领取的券 |

**未调用的写操作**：`create-order`、`cancel-order`、`auto-bind-coupons`。本 Skill 全流程只读，下单环节交给人确认后执行。

---

## 3. 真实调用流程

以「2026-10-23 晚餐，华旭国际大厦店，4 人」为例。

### Step 1 · 定位门店

```
query-nearby-stores({searchType: 2, beType: 1, city: "上海市", keyword: "人民广场"})
```

返回首条：`storeCode=1450713`、`麦当劳上海黄浦华旭国际大厦餐厅`、`南京东路街道西藏中路336号`、`distance=144`，
`reservationTimeOptions` 含当前餐段「夜市 17:14–21:45」。

> `searchType=1`（收藏门店）在收藏为空时返回 `code=600050`，文案「收藏餐厅列表为空」，不是故障。

### Step 2 · 拉菜单

```
query-meals({storeCode: "1450713", beType: 1, orderType: 1})
```

122 项在售。每项含 `currentPrice`、`originalPrice`、`discountType`、`canWithOrder`。

### Step 3 · 补热量

```
list-nutrition-foods({})
query-meal-detail({storeCode: "1450713", beType: 1, orderType: 1, code: "9900005456"})
```

营养表解析出 **160 行数据、158 个唯一菜名**（「小杯玉米杯」「纯牛奶（盒装）」各有重复），
菜单 122 项能对上 **43 个条目**，扣掉 1 个 0 kcal 的「纯悦」水，**有正热量的 42 项**。
套餐靠 `query-meal-detail` 拆 `rounds/choices` 后逐项累加。

### Step 4 · 查券

```
query-store-coupons({storeCode: "1450713", beType: 1, orderType: 1})
```

返回当前门店可用的券，每张券的 `products[].productCode` 即唯一合法使用对象。

### Step 5 · 算价与券分配

```
calculate-price({
  storeCode: "1450713", beType: 1, orderType: 1,
  items: [
    {productCode: "9900015602", quantity: 1},
    {productCode: "9900000890", quantity: 1},
    {productCode: "9900000888", quantity: 1},
    {productCode: "9900016523", quantity: 1,
     couponId: "<YOUR_COUPON_ID>", couponCode: "<YOUR_COUPON_CODE>"}
  ],
  withOrder: {membershipCode: "106", specId: "880"}
})
```

四组实测结果（同一批商品，只改是否买卡、是否用券）：

| 组合 | 原价 | 实付 | 项数 | 说明 |
|---|---:|---:|---:|---|
| 不买卡 + 「"辣"么快乐套餐」 | ¥162.40 | ¥162.40 | 4 | 没有折扣，照价付 |
| 买卡 + 「"辣"么快乐套餐」 | ¥172.30 | ¥116.80 | 5 | 卡本身花钱，但省 ¥55.50 |
| 买卡 + 券后龙焰两件套 | ¥182.30 | **¥113.28** | 5 | 券在上一条基础上再省 ¥3.52 |
| 不买卡 + 券后龙焰两件套 | ¥172.40 | ¥158.88 | 4 | 不买卡时，券也救不回来 |
| 买卡 + 龙焰**不带券** | ¥148.30 | ¥92.80 | **4** | ⚠ 商品被静默丢弃，比上一行便宜 ¥20.48 |

最后一行不是「更省」，是**少了一个人的饭**：92.80 与 113.28 的差额 20.48，正好等于那道菜券后的单价。不带券调 `calculate-price` 不报错、不提示，`productList` 直接少一项。

### Step 6 · 输出清单

求解结果 → `scripts/team_feed.py --json` → `scripts/render_card.py` → 图片卡片。
渲染层不生成任何业务数字。

---

## 4. 三个由真实调用才暴露的问题

这三点决定了「照文档写」和「真跑过」的差别，也是本项目的主要技术含量。

### 4.1 菜单价 ≠ 结算价

`query-meals` 里「板烧鸡腿堡四件套」显示 `currentPrice = 28`，但 `discountType = "随单购麦金卡优惠"`。
**不带 `withOrder` 直接算价，返回 ¥57**。122 项里有 **19 项**是这种。

这意味着：任何直接把 `currentPrice` 拿去做比价或求和的实现，这 19 项的价格都是错的。
正确做法是维护两条账——买卡 / 不买卡，把卡钱 `¥9.9` 计入后再比较。

### 4.2 券专属商品不在菜单里，且会被静默丢弃

「龙焰鸡腿堡两件套」(`9900016523`) 在 122 项菜单中查不到，只在带 `couponId` 时才存在。

反过来更危险：**不带券调用 `calculate-price`，这个商品不会报错，而是从 `productList` 里直接消失**。
实测同一篮子：带券 5 项 ¥113.28，不带券 4 项 ¥92.80——少了 ¥20.48，正好是那道菜的券后单价。
从数字上看，它反而像「省得更多」。所以算完价必须核对 `productList` 的商品数与数量是否与入参一致。

### 4.3 营养数据不是结构化 JSON

`list-nutrition-foods` 的 `data` 字段是一张字符串表格：
`名称,描述,能量kJ,能量kcal,蛋白质,脂肪,碳水,钠,钙`。必须先解析再按名称匹配，
且存在重名（同一菜名出现两次会互相覆盖）。

---

## 5. 业务价值

| 维度 | 说明 |
|---|---|
| 解决的场景 | 「被派去买全组午饭」——多人异质约束 + 只有下单者一人有券 + 需要可确认的交付物 |
| 用到的 MCP 能力 | 门店（1）、菜单（2）、营养（1）、券（2）、算价（1），共 7 个 Tool 组成闭环 |
| 为什么必须用 MCP | 价格、券池、门店在售状态都是实时变化的，离线方案无法给出「实付」 |
| 可复现性 | `scripts/menu.json` 是真实快照，离线可跑；所有金额在 README 与 `examples/demo-card.md` 中对账 |

---

## 6. 已知边界

- 券绑定 Token 所属账号，替同事点单时只能用下单者自己的券。这一点在 SKILL.md 中明确要求告知用户。
- `order-list` 只返回最近 10 笔订单，所以没做「年度报告」类功能。
- 团餐 `beType=6` 的 `query-meal-assistance` 已确认可调用，但未接入流程。
- 券后价目前需由 `calculate-price` 实测后手工填入 `coupons.json`，不做标题猜价。
