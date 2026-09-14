# 05 — 合并重复 Speaker、修正历史颜色并通过发布闸门

**What to build:** 当在线状态机仍把同一个人拆成两个正式 ID 时，系统按严格、确定性的 profile 证据自动合并，并通过 cue revision 修正保留窗口内的字幕颜色，同时完成遥测、配置和最终 benchmark 收口。

**Blocked by:** 04 — 用迟滞、候选确认和多 Prototype 阻止语调分裂

**Status:** release blocked

> 实现已完成，但旧 CREMA-mini gate 无效：其 legacy baseline 复现的是 over-merge，且绝对 B³ 目标超过实测 oracle-k 天花板（约 0.66–0.69）。当前 benchmark 会在症状准入处 fail fast；待真实日语直播 smoke set / 合格日语 benchmark 后再判 release。

- [x] reconciliation 按每 20 个 observation 或 30 秒运行，使用标定后的 cross-support、compactness、competitor margin 和两次 proposal 规则。
- [x] 注入的 confirmed overlap 建立双向 cannot-link 并阻止合并；首版不宣称生产环境能检测 overlap，没有 overlap 不增加 merge score。
- [x] 合并保留更早 confirmed ID，使用 medoid + farthest-first 压缩为最多四个 prototype，并保持 last speaker、candidate 引用和 proposal 状态一致。
- [x] CueStore 的 speaker remap 幂等；只修改 old ID cue，每个实际修改推进 revision/seq，重复调用不增长 seq。
- [x] 播放器收到同 cue 的更高 seq 和新 speaker 后只更新颜色，不创建重复 cue、不重置文本和排期。
- [x] 状态接口报告 queue、timestamp inversion、unknown/tentative、candidate、profile update、profile 平均观测数、reconciliation/merge 和 latency，不暴露 embedding。
- [ ] 最终 evaluation：实现已改成 ceiling ratio、UNKNOWN<10%、真实处理延迟和 queue drain；因当前数据集未通过症状准入，release blocked。
- [x] 现有字幕、翻译、cue polling 和 web asset 测试保持通过；未通过闸门的可选 scorer 默认关闭。
