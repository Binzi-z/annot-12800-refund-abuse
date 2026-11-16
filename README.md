# 退款套现检测引擎

基于客服工单与退款的配对关系识别套现，规则参数化、版本化，告警可回溯。

```
data/events.jsonl              事件流（只读，格式不改）
engine.py                      CLI 入口
refund_abuse/
  ids.py                       原生字段别名 + 确定性派生编号
  rules.py                     版本化规则与参数（RuleConfig / RULE_VERSION）
  model.py                     事件→订单/退款/工单配对→规则求值
  engine.py                    编排、输出、旧告警解释
rules.example.json             规则参数覆盖示例（调阈值不用发版）
tests/fixtures/native_events.jsonl  含订单号/退款流水/工单号的标注夹具
tests/                         28 个测试
```

## Run

```bash
python -I -m pytest tests -q
python -I engine.py data/events.jsonl alerts.json
python -I engine.py data/events.jsonl alerts.json --rules rules.example.json
python -I engine.py --explain alerts.json
```

## 两条检测规则（rule set 1.0.0）

| 规则 | 套现形态 | 触发条件 |
|---|---|---|
| `same_order_multi_refund` | 同一订单多次退款 | 同 `order_id` 退款笔数 ≥ 2 且退款总额 > 订单金额，且每笔退款关联原生工单 |
| `near_full_refund_open_order` | 退款接近订单金额但订单未关闭 | 订单未处于 closed/refunded 等售后终态、退款总额 ≥ 90% 订单金额，且每笔退款关联原生工单 |

零误报护栏：

- 订单已关闭（含 `closed_refunded`）的全额/高额退款是正常售后，一律不告警；
- 没有原生工单关联的退款无法与普通退款区分，一律不告警（两条规则共同要求）；
- 单笔部分退款、多笔但合计未达阈值的退款不告警；
- 两条规则互斥：超额多次退款只走 `same_order_multi_refund`，不重复告警。

每条告警含 `order_id`、`refund_ids`（退款流水）、`ticket_ids`（工单编号）、
`account`、触发时的完整窗口证据（订单金额/状态/每笔退款时间与金额）、
`rule_version` 与冻结的 `rule_params`。

## 关于当前事件流（重要）

`data/events.jsonl` 只有 `ts/account/device/amount/action/ip_region` 六个字段，
没有订单号、退款流水、工单号与订单状态；其中 772 笔退款与 769 笔支付是独立生成的，
不存在真实配对。引擎因此分两种模式：

- **原生模式**：事件携带可选的 `order_id` / `refund_id` / `ticket_id` /
  `order_status` 字段（属于字段叠加，不改变既有格式）。规则只在该模式生效，
  用 `tests/fixtures/native_events.jsonl` 验证：3 个套现样例全命中、5 个正常/
  售后样例零误报。
- **派生模式**：当前六字段数据。按 (账号, 金额) FIFO 重建订单，并为每笔退款/
  工单生成 `ORD-DER-*` / `REF-DER-L*` / `TKT-DER-*` 确定性编号供分析，
  但规则**不触发**——在没有真实序列号的流上告警必然误伤正常退款。
  输出的 `data_support` 会明确说明这一点及事件计数。

## 规则版本与可解释性

- 语义/阈值改动按 semver 提升 `refund_abuse/rules.py` 中的 `RULE_VERSION`
  （major=语义变化，minor=新规则，patch=阈值微调）；
- 每条告警内嵌版本号与参数快照，告警文件还记录输入流的 sha256 与事件数；
- `--explain <旧告警文件>` 用当前引擎复核旧告警：同 major 视为可解释，
  并回放触发指标与窗口证据；major 不一致会明确标记，而不是静默套用新规则；
- 阈值调整走 `--rules` 参数文件，无需改代码发版；未知参数直接拒绝。

## 说明

`labels.json` 与本引擎无关，是上一版场景（burst pay / 新设备大额提现）的残留标注，
保留不动。
