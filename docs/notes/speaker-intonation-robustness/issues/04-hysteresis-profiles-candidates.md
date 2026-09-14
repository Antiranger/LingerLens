# 04 — 用迟滞、候选确认和多 Prototype 阻止语调分裂

**What to build:** 将一次性的“低于阈值就建新人”改为 confirmed/tentative/unknown 状态机，让同一个人的不同语调由少量质量受控 prototype 表示，只有积累足够证据的新声音才获得正式颜色。

**Blocked by:** 01 — 建立 CREMA-D Mini Ground-Truth Benchmark; 02 — 将 Speaker Attribution 收口到单一有序 Worker; 03 — 用 Pooled Embedding 和可标定 Scorer 稳定跨语调分数

**Status:** complete — verified 2026-08-31 (real CREMA-D comparison pending audio)

- [x] assignment 返回 confirmed existing/new、tentative 或 unknown，并带 best/second score、margin、reason 和 profile-update permission。
- [x] 按 spec 实现高阈值、ambiguity band 和只在模糊区生效的 continuity 规则；continuity-only 不更新 profile。
- [x] 短句无历史时 UNKNOWN，短句不能创建 candidate；达到 speaker 容量后新人保持 UNKNOWN，正式 ID 会话内不复用。
- [x] candidate 按 spec 的 top-2 score、margin、硬 10-ordinal 生命周期、两句/三秒/内部一致性规则关联和确认；质量标记 observation 不进入 candidate/profile。
- [x] candidate 确认后获得从未使用过的最小 ID，并回填仍在 store 的 waiting cues；benchmark seam 同样回填历史 assignment。
- [x] 每个 speaker 最多四个 prototype，准入、EMA、新模式新增和冗余替换严格采用 spec 的确定性规则。
- [x] 单个极端语调离群不会产生正式新 ID；不同真人仍能被确认成不同 ID。
- [x] robust benchmark seam 可比较 split/churn/merge/UNKNOWN，并有 false-split 与 false-merge 防作弊 fixture；真实 evaluation 因 CREMA-D 音频缺失未运行，故未声称满足数值发布闸门。

**Verification:** independent reviewer findings fixed (quality-flag pollution, hard candidate TTL, persistent new-speaker ceiling, benchmark candidate backfill, false-split fixture). Speaker tests 32 passed; pipeline tests 25 passed; benchmark tests 7 passed; core LSP diagnostics clean.
