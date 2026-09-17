# Provider option wiring audit — 2026-09-17

配套报告：[性能审计](performance-audit-2026-09-17.md)。本表覆盖静态识别到的 88 个顶层选项键：适配器的 `options.get`、下标读取、存在性检查、字面量键映射，外加前端编辑字段及服务端转发选项。嵌套的 `turnDetection` 子键不独立计数。模板列只统计 Python 内置模板；前端切换协议的 defaults 另见报告。这是代码连接清单，不表示每一种服务协议都已联网验证。

所有配置都可以通过完整 Provider catalog 保存路径写入：`config.py:update_model_settings` 会复制、校验并保存 options。因此“没有编辑框/模板默认值”不等于选项无效，也不等于没有写入路径。表内行号对应本轮工作区快照，基线提交 b56582f。

唯一的“有编辑框、无消费者”是 `language`：转写实际读取全局 SourceLanguagePolicy。唯一的“内置模板写入、无消费者”是 `hotwordsEnabled`：实际热词能力检查 `vocabulary`/`vocabularyId`。

| 选项 | 读取位置 | Python 模板赋值 | 编辑器字段 |
|---|---|---|---|
| `appId` | asr_tencent_asr.py:152, asr_tencent_asr.py:217 | config.py:193 | player.js:1423 |
| `appKey` | asr_volcengine_sauc.py:194 | 无（可由 JSON/catalog 设置） | player.js:1411 |
| `authMode` | asr_volcengine_sauc.py:191 | 无（可由 JSON/catalog 设置） | player.js:1412 |
| `closeDrainTimeoutSeconds` | asr_assemblyai_streaming.py:166, asr_soniox_realtime.py:162, asr_speechmatics_realtime.py:124, asr_volcengine_sauc.py:186 | config.py:139, config.py:155 | 无 |
| `commitStrategy` | asr_elevenlabs_scribe_realtime.py:108 | config.py:166 | player.js:1414 |
| `contextEnabled` | asr_dashscope_task.py:185, asr_dashscope_task.py:99 | config.py:33, config.py:87 | 无 |
| `contextPairs` | config.py:514, server.py:1050 | config.py:249 | player.js:1440 |
| `contextSeconds` | server.py:1051 | config.py:250 | 无 |
| `corpus` | asr_volcengine_sauc.py:219, asr_volcengine_sauc.py:220 | 无（可由 JSON/catalog 设置） | 无 |
| `delay` | asr_openai_realtime_transcription.py:147, asr_openai_realtime_transcription.py:148 | config.py:209 | 无 |
| `diarization` | asr_speechmatics_realtime.py:137, asr_speechmatics_realtime.py:95 | 无（可由 JSON/catalog 设置） | player.js:1419 |
| `diarize` | asr_deepgram_streaming.py:125, asr_deepgram_streaming.py:178 | 无（可由 JSON/catalog 设置） | player.js:1402 |
| `enableEndpointDetection` | asr_soniox_realtime.py:183 | config.py:120 | player.js:1397 |
| `enableItn` | asr_volcengine_sauc.py:210 (mapping) | config.py:153 | 无 |
| `enableLanguageIdentification` | asr_soniox_realtime.py:182 | config.py:119 | player.js:1398 |
| `enableNonstream` | asr_volcengine_sauc.py:214 (mapping) | 无（可由 JSON/catalog 设置） | 无 |
| `enablePartials` | asr_speechmatics_realtime.py:135 | config.py:178 | player.js:1418 |
| `enablePunc` | asr_volcengine_sauc.py:205 | 无（可由 JSON/catalog 设置） | 无 |
| `enableSpeakerDiarization` | asr_soniox_realtime.py:105, asr_soniox_realtime.py:185 | config.py:122 | player.js:1399 |
| `enableSpeakerInfo` | asr_volcengine_sauc.py:213 (mapping) | 无（可由 JSON/catalog 设置） | 无 |
| `endOfUtteranceSilenceTriggerSecs` | asr_speechmatics_realtime.py:144, asr_speechmatics_realtime.py:146 | 无（可由 JSON/catalog 设置） | 无 |
| `endWindowSize` | asr_volcengine_sauc.py:211 (mapping) | config.py:154 | 无 |
| `endpointLatencyAdjustmentLevel` | asr_soniox_realtime.py:197 (mapping) | 无（可由 JSON/catalog 设置） | 无 |
| `endpointSensitivity` | asr_soniox_realtime.py:196 (mapping) | config.py:124 | 无 |
| `endpointingMs` | asr_deepgram_streaming.py:173 | config.py:102 | player.js:1403 |
| `engineModelType` | asr_tencent_asr.py:107, asr_tencent_asr.py:188 | config.py:195 | player.js:1425 |
| `filterBackgroundAudio` | asr_elevenlabs_scribe_realtime.py:197 | 无（可由 JSON/catalog 设置） | 无 |
| `filterEmptyResult` | asr_tencent_asr.py:204, asr_tencent_asr.py:205 | 无（可由 JSON/catalog 设置） | 无 |
| `heartbeat` | asr_dashscope_task.py:172, asr_dashscope_task.py:173 | config.py:31, config.py:67 | player.js:1429 |
| `hotwordsEnabled` | **无** | config.py:32 | 无 |
| `includeLanguageDetection` | asr_elevenlabs_scribe_realtime.py:117, asr_elevenlabs_scribe_realtime.py:190 | config.py:167 | player.js:1416 |
| `includeTimestamps` | asr_elevenlabs_scribe_realtime.py:188 | 无（可由 JSON/catalog 设置） | player.js:1415 |
| `interimResults` | asr_deepgram_streaming.py:171 | config.py:99 | 无 |
| `keepAliveSeconds` | asr_assemblyai_streaming.py:157, asr_deepgram_streaming.py:162, asr_soniox_realtime.py:152 | config.py:108, config.py:125 | 无 |
| `keyterms` | asr_elevenlabs_scribe_realtime.py:192, asr_elevenlabs_scribe_realtime.py:193 | 无（可由 JSON/catalog 设置） | 无 |
| `keytermsPrompt` | asr_assemblyai_streaming.py:200, asr_assemblyai_streaming.py:201 | 无（可由 JSON/catalog 设置） | 无 |
| `language` | **无** | 无（可由 JSON/catalog 设置） | player.js:1393 |
| `languageHintsStrict` | asr_soniox_realtime.py:171, asr_soniox_realtime.py:174 | 无（可由 JSON/catalog 设置） | 无 |
| `languages` | asr_dashscope_task.py:83 | config.py:25, config.py:63, config.py:81 | 无 |
| `maxDelaySeconds` | asr_speechmatics_realtime.py:142, asr_speechmatics_realtime.py:143 | config.py:179 | player.js:1421 |
| `maxEndpointDelayMs` | asr_soniox_realtime.py:195 (mapping) | config.py:123 | player.js:1400 |
| `maxNonFinalTokensDurationMs` | asr_soniox_realtime.py:200 (mapping) | 无（可由 JSON/catalog 设置） | 无 |
| `maxPendingWindows` | asr_openai_transcriptions.py:69 | 无（可由 JSON/catalog 设置） | 无 |
| `maxSentenceSilence` | asr_dashscope_task.py:168 | config.py:27, config.py:65, config.py:83 | 无 |
| `maxSpeakTimeMs` | asr_tencent_asr.py:202, asr_tencent_asr.py:203 | 无（可由 JSON/catalog 设置） | 无 |
| `maxSpeakers` | asr_assemblyai_streaming.py:188, asr_assemblyai_streaming.py:189, asr_speechmatics_realtime.py:139 | config.py:138 | player.js:1408, player.js:1420 |
| `maxTokens` | config.py:512, mt_anthropic_messages.py:68, mt_google_genai.py:83, mt_openai_compat.py:94 | config.py:247 | player.js:1438 |
| `maxTurnSilenceMs` | asr_assemblyai_streaming.py:194, asr_assemblyai_streaming.py:195 | 无（可由 JSON/catalog 设置） | 无 |
| `minSilenceDurationMs` | asr_elevenlabs_scribe_realtime.py:186, asr_elevenlabs_scribe_realtime.py:187 | 无（可由 JSON/catalog 设置） | 无 |
| `minSpeechDurationMs` | asr_elevenlabs_scribe_realtime.py:184, asr_elevenlabs_scribe_realtime.py:185 | 无（可由 JSON/catalog 设置） | 无 |
| `minTurnSilenceMs` | asr_assemblyai_streaming.py:192, asr_assemblyai_streaming.py:193 | 无（可由 JSON/catalog 设置） | 无 |
| `mode` | asr_assemblyai_streaming.py:190, asr_assemblyai_streaming.py:191 | config.py:136 | player.js:1406 |
| `multiThresholdModeEnabled` | asr_dashscope_task.py:170, asr_dashscope_task.py:171 | config.py:30, config.py:66 | 无 |
| `needvad` | asr_tencent_asr.py:198, asr_tencent_asr.py:199 | config.py:198 | 无 |
| `noVerbatim` | asr_elevenlabs_scribe_realtime.py:195 | 无（可由 JSON/catalog 设置） | 无 |
| `outputZhVariant` | asr_volcengine_sauc.py:215 (mapping) | 无（可由 JSON/catalog 设置） | 无 |
| `prompt` | asr_assemblyai_streaming.py:198, asr_assemblyai_streaming.py:199 | 无（可由 JSON/catalog 设置） | 无 |
| `reasoningEffort` | mt_openai_compat.py:150 | 无（可由 JSON/catalog 设置） | 无 |
| `requestTimeoutSeconds` | asr_openai_transcriptions.py:70 | 无（可由 JSON/catalog 设置） | player.js:1395 |
| `resourceId` | asr_volcengine_sauc.py:198 | config.py:152 | player.js:1410 |
| `responseFormat` | asr_openai_transcriptions.py:131 | 无（可由 JSON/catalog 设置） | 无 |
| `resultType` | asr_volcengine_sauc.py:207 | 无（可由 JSON/catalog 设置） | 无 |
| `sampleRate` | asr_dashscope_task.py:82, asr_openai_transcriptions.py:42, server.py:982 | config.py:24, config.py:46, config.py:62, config.py:80 | 无 |
| `secretId` | asr_tencent_asr.py:150, asr_tencent_asr.py:191 | config.py:194 | player.js:1424 |
| `semanticPunctuationEnabled` | asr_dashscope_task.py:167 | config.py:26, config.py:64, config.py:82 | 无 |
| `sessionHeartbeatSeconds` | asr_assemblyai_streaming.py:196, asr_assemblyai_streaming.py:197 | 无（可由 JSON/catalog 设置） | 无 |
| `showUtterances` | asr_volcengine_sauc.py:206 | 无（可由 JSON/catalog 设置） | 无 |
| `signatureTtlSeconds` | asr_tencent_asr.py:189 | 无（可由 JSON/catalog 设置） | 无 |
| `smartFormat` | asr_deepgram_streaming.py:172 | config.py:100 | 无 |
| `speakerLabels` | asr_assemblyai_streaming.py:128, asr_assemblyai_streaming.py:186 | config.py:137 | player.js:1407 |
| `speechNoiseThreshold` | asr_dashscope_task.py:174, asr_dashscope_task.py:175 | 无（可由 JSON/catalog 设置） | 无 |
| `startTimeoutSeconds` | asr_dashscope_task.py:197 | config.py:34, config.py:68, config.py:88 | 无 |
| `temperature` | config.py:511, mt_anthropic_messages.py:69, mt_google_genai.py:82, mt_openai_compat.py:93 | config.py:246 | player.js:1437 |
| `timeoutSeconds` | config.py:513, http.py:44, server.py:1049 | config.py:248 | player.js:1439 |
| `tmPairs` | mt_openai_compat.py:156, mt_qwen_mt.py:105 | 无（可由 JSON/catalog 设置） | 无 |
| `turnDetection` | asr_openai_realtime_transcription.py:170, asr_qwen_realtime.py:87, server.py:1047 | config.py:210, config.py:221, config.py:47 | 无 |
| `uid` | asr_volcengine_sauc.py:222 | 无（可由 JSON/catalog 设置） | 无 |
| `utteranceEndMs` | asr_deepgram_streaming.py:183, asr_deepgram_streaming.py:184 | config.py:106 | player.js:1404 |
| `vadEvents` | asr_deepgram_streaming.py:174 | config.py:103 | 无 |
| `vadSegmentDurationMs` | asr_volcengine_sauc.py:212 (mapping) | 无（可由 JSON/catalog 设置） | 无 |
| `vadSilenceThresholdSecs` | asr_elevenlabs_scribe_realtime.py:182, asr_elevenlabs_scribe_realtime.py:183 | 无（可由 JSON/catalog 设置） | 无 |
| `vadSilenceTimeMs` | asr_tencent_asr.py:200, asr_tencent_asr.py:201 | 无（可由 JSON/catalog 设置） | 无 |
| `vadThreshold` | asr_elevenlabs_scribe_realtime.py:180, asr_elevenlabs_scribe_realtime.py:181 | 无（可由 JSON/catalog 设置） | 无 |
| `vocabulary` | asr_dashscope_task.py:181, asr_dashscope_task.py:182, asr_dashscope_task.py:98 | 无（可由 JSON/catalog 设置） | 无 |
| `vocabularyId` | asr_dashscope_task.py:179, asr_dashscope_task.py:180, asr_dashscope_task.py:98 | 无（可由 JSON/catalog 设置） | player.js:1428 |
| `voiceFormat` | asr_tencent_asr.py:193 | config.py:196 | 无 |
| `windowSeconds` | asr_openai_transcriptions.py:35, asr_openai_transcriptions.py:68 | 无（可由 JSON/catalog 设置） | player.js:1394 |
| `wordInfo` | asr_tencent_asr.py:126, asr_tencent_asr.py:196, asr_tencent_asr.py:197 | config.py:197 | player.js:1426 |
