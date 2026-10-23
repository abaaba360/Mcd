# 工具契约（实测版）

> 全部参数名与行为都在 2026-10-23 对 `https://mcp.mcd.cn` 实跑确认过。
> 官方文档在 `open.mcd.cn/mcp/doc`，下面的「坑」列是文档没写的。

## 通用事实

| 项 | 值 |
|---|---|
| 地址 | `https://mcp.mcd.cn` |
| 协议 | Streamable HTTP |
| 鉴权 | `Authorization: Bearer <TOKEN>` |
| 限流 | 600 次/分钟，超限 429 |
| 错误码 | 401（Token 无效）、429（限流） |
| 线上工具数 | **35**（文档写 33） |
| 返回格式 | 「字段说明 + JSON」混合文本，且**标记不统一**（见下方解析坑） |

## 解析坑（比参数更容易踩）

| 接口 | 返回形态 | 正确做法 |
|---|---|---|
| 多数接口 | `## Original Response` + JSON | 找到标记后取 JSON |
| `query-store-coupons` | **没有标记**，末尾直接跟裸 JSON，正文含 `{data[].title}` 伪 JSON | 逐个 `{` 位置试探 `raw_decode`，取能解出且含 `success/code/data` 的那个 |
| `list-nutrition-foods` | `data` 是**字符串表格**，不是数组 | 按行 `split(",")`，严格 9 列才收 |

## 核心 16 个工具

### query-nearby-stores — 找门店

```json
{"searchType": 2, "beType": 1, "city": "上海市", "keyword": "人民广场"}
```

| searchType | 含义 | 必填 |
|---|---|---|
| 1 | 收藏的门店（默认） | 无 |
| 2 | 城市 + 关键词 | `city`, `keyword` |

- ⚠️ **没有 `searchType=3`，`inputSchema` 里也没有 `longitude` / `latitude`**。想传经纬度没有入口，别硬试。

返回 `storeCode`（纯数字）、`address`、`distance`、`reservation`、`reservationTimeOptions[]`。`beCode` 只在得来速（beType=5）和外送（orderType=2）场景才需要。

- 坑：`searchType=1` 且收藏为空时返回 `code=600050`，文案是「收藏餐厅列表为空」，**不是故障**。

### query-meals — 门店菜单

```json
{"storeCode": "1450713", "orderType": 1, "beType": 1}
```

返回 `data.categories[].meals[].code`（含 tags）+ `data.meals[code]`（`name` / `currentPrice` / `originalPrice` / `discountType` / `canWithOrder` / `withOrder`）。

- 实测规模：单店 122 项。
- 坑：`currentPrice` 可能是「随单购麦金卡优惠」价，不买卡结算翻倍。

### query-meal-detail — 套餐拆解

```json
{"storeCode": "1450713", "orderType": 1, "beType": 1, "code": "9900005456"}
```

返回 `data.rounds[]`，每 round 含 `name`、`choices[]`（`code` / `name` / `isDefault` / `diffPrice`）。

- 坑：参数名是 **`code`**，不是 `productCode`。传错报 `400 code不得为空白`。

### list-nutrition-foods — 营养表

无入参。`data` 是**字符串**，内容为定宽表格：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
```

- 实测 **160 行数据**，但只有 **158 个唯一菜名**（「小杯玉米杯」「纯牛奶（盒装）」各重复一次）。
- 以**单品**为主，少数套餐也自带条目（如板烧鸡腿堡四件套 391 kcal）；菜单里 122 项能对上 43 个条目，扣掉 1 个 0 kcal 的「纯悦」水，**有正热量的 42 项**。
- 其余套餐要先用 `query-meal-detail` 拆出子项，再按名称累加。

### query-store-coupons — 当前门店可用券

```json
{"storeCode": "1450713", "orderType": 1, "beType": 1}
```

返回 `data[]`：`title` / `couponId` / `couponCode` / `tradeDateTime` / `products[].productCode` / **`products[].productName`**。

- 券**绑定商品**，`products[].productCode` 是唯一合法使用对象。
- 同一 `couponId` 可能对应多个 `couponCode`，按 couponId 分组展示。
- **返回里没有券后价字段**，也没有 discount。券后价只能靠 `calculate-price` 带券算一次再读 `productList`。
- **返回没有 `## Original Response` 标记**（见上方解析坑）。
- `products[].productName` 是券专属商品的唯一名字来源，这类商品在 `query-meals` 里查不到。

### calculate-price — 算价（全链路的地基）

```json
{
  "storeCode": "1450713", "orderType": 1, "beType": 1,
  "withOrder": {"membershipCode": "106", "specId": "880"},
  "items": [
    {"productCode": "9900016523", "quantity": 1, "couponId": "...", "couponCode": "..."}
  ]
}
```

返回 `productOriginalPrice` / `productPrice` / `discount` / `price`（**单位：分**）/ `productList[]` / `takeWayList[]`。

- 坑 1：不带券的券专属商品会被**静默丢弃**，`productList` 少一项且不报错。
- 坑 2：`withOrder` 是从 `query-meals` 的 `data.meals[code].withOrder` **原样复制**，不是自己拼。
- `takeWayList[].code`（`eat-in` / `locker-out`）是下单必填的 `takeWayCode` 来源。

### create-order — 下单（写操作）

`inputSchema` 的 `required` 只有 `storeCode` / `orderType` / `beType`，但实际下单还必须给 `items`；到店另需 `takeWayCode`，外送另需 `addressId`，团餐另需 `gmServiceCode`。返回订单详情 + 支付链接。

- **付款由人完成**，Agent 只建单。
- 调用前必须让用户逐项确认清单。

### 其余常用

| 工具 | 用途 | 备注 |
|---|---|---|
| `query-my-coupons` | 卡包里的券 | 不区分门店/渠道 |
| `available-coupons` | 麦麦省可领券 | 需登录态 |
| `auto-bind-coupons` | 一键领券 | **写操作**，会改用户资产 |
| `query-my-account` | 积分账户 | 可用/累计/冻结/临期 |
| `order-list` | 历史订单 | 只返最近 **10** 笔 |
| `query-order` | 订单详情 | 需 `orderId` |
| `cancel-order` | 取消订单 | **写操作** |
| `now-time-info` | 服务器时间 | 判断当前餐段 |
| `query-meal-assistance` | 团餐助餐服务 | `beType` 固定传 **6**；只有 `storeCode` 必填 |
| `query-promotions` | 团餐促销规则 | **文档未收录** |
| `query-survey-coupon` | 问卷券 | **文档未收录** |

**线上改名的两个工具**（照着官方文档的写法调不到）：

| 文档里写的 | 线上实际叫 |
|---|---|
| `query-partystore-date` | `query-party-store-date` |
| `query-partystore-session` | `query-party-store-session` |

## 参数速查（易错项）

| 场景 | orderType | beType | beCode | 另需 |
|---|---|---|---|---|
| 到店自取 | 1 | 1 | **不传** | — |
| 得来速 | 1 | 5 | 必传（门店查询返回） | — |
| 麦乐送 | 2 | 2 | 不传 | `addressId`（`delivery-query-stores` 必填项） |
| 企业团餐 | 2 | 6 | 必传 | `gmServiceCode`（来自 `query-meal-assistance`） |

`delivery-query-stores` 的 required 实测是 `beType` + `addressId`，不是 beCode。
