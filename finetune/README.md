# IRDS 本地 AI 顾问 · LoRA 微调数据与脚本

现阶段没有 GPU，线上用“知识库检索 + Skill”。这里先把微调需要的数据和脚本备好，借到带显卡的机器后可以直接训练。
**全程离线**：数据从本仓库的规则、知识文档和公开统计模板化生成；可选的问题改写只调用本机 Ollama（127.0.0.1），不调用任何云端 API。

## 目录

| 文件 | 说明 |
|---|---|
| `build_sft_dataset.py` | 生成数据集（模板化，可选 `--paraphrase N` 用本机 qwen3.5:4b 改写 N 条问题） |
| `data/train.jsonl` / `data/val.jsonl` | chat 格式 SFT 数据，每行 `{"messages":[user, assistant], "category":…}`；单文件超过 900 KB 时自动分片为 `train-01.jsonl`… |
| `data/system_prompt.md` | 固定 system 提示词（= 当前 Skill + 引用规则）。为控制文件大小不逐行重复，训练脚本会把它插到每条样本最前面 |
| `data/stats.json` | 条数、类别分布、JSON 模式与拒答条数 |
| `train_lora.py` | LoRA / QLoRA 训练脚本（unsloth 或 peft + trl） |
| `export_to_ollama.sh` | 合并 LoRA → 转 GGUF → 量化 Q4_K_M → `ollama create irds-advisor:4b` |

## 当前数据量

466 条（模板 366 + 本机 qwen3.5:4b 改写问法 100，去重后），训练 418 / 验证 48；JSON 模式 119 条，拒答 40 条（JSON 28 + 文本 12），无依据 15 条。分类见 `data/stats.json`。改写样本可能有轻微语义漂移（例如“车险”被改成“商业车险”），训练前建议人工抽检。

## 数据设计

user 消息与线上推理提示词同构：`【知识库片段】[K编号] 来源｜章节 + 正文`（正确片段与 1–2 条干扰片段打乱）→ `【系统计算结果】` → `【问题】`，JSON 模式在末尾附 Schema 指令。
assistant 只引用给出的 `[K编号]` / `[系统]`，数字逐字取自片段或系统结果，专门强化 4B 小模型最容易犯的三类错误：自己算数、引用不存在的编号、该拒答时不拒答。

| 类别 | 来源 | 说明 |
|---|---|---|
| 规则 / 校验 / 口径 | `config/alert_rules.yaml`、`data_quality_rules.yaml`、`metric_definitions.yaml` | 每条规则 2–3 种问法；未启用规则（R009）教模型“不得自行推断” |
| 判级解释 | `src/alerts/thresholds.py::_level` 实算 | 90/95/100 边界（严格大于）、红 > 橙 > 黄、待确认状态 |
| 预警解释（JSON） | `data/official/brvehins_2011.xlsx` 真实州 × 车型行 + 系统判级 | 结论 / 依据 / 建议 / 待核查，费用率假设、雷亚尔单位 |
| 行业对标 | `data/official/china/*.csv` | NFRA 车险保费、单均保费、省级财产险赔付率（含口径提示）、精协基准纯风险保费、汽车保有量 |
| 知识文档 | `knowledge/平台规则/*.md` | 三级阈值、11.1 修复口径、三要素归因、行业对标口径 |
| 拒答 | Skill “拒答与转人工” | 无关问题、外发数据、改阈值 / 改等级、人伤定责（refused=true） |
| 无依据 | 行业对标“缺口” | 渤海财险指标、省级频度、2024 年后单均保费等：说明“知识库中没有相关依据”（refused=false） |
| 不自算 | 系统 what-if | 有系统假设结果就引用 `[系统]`，没有就回答“需由系统重新计算” |

重新生成（知识库或规则更新后）：

```bash
.venv/bin/python finetune/build_sft_dataset.py                 # 纯模板，约 1 秒
.venv/bin/python finetune/build_sft_dataset.py --paraphrase 80 # 加 80 条本机改写问法（约 5–10 分钟）
```

去重键 = 规范化问题 + 系统结果 + 模式；按类别分层 9:1 切分训练 / 验证，随机种子固定。

## 训练（需要 GPU）

| 方案 | 显存 | 参考显卡 | 时长（约 400 条 × 3 epoch） |
|---|---|---|---|
| QLoRA 4bit（默认） | 10–12 GB | RTX 3060 12G、4070、T4 16G | 4090 约 10–15 分钟；T4 约 40–60 分钟 |
| LoRA bf16（`--no-4bit`） | 20–24 GB | RTX 3090 / 4090、A10 | 略快于 QLoRA |

默认超参：rank 16、alpha 32、lr 2e-4、cosine、3 epoch、序列 2048、有效 batch 16，目标模块为全部注意力和 MLP 投影；只对 assistant 回复计算损失（unsloth 后端）。

```bash
# 1) 在有网络的环境提前下载基础模型到本地目录，然后拷到训练机（训练时离线）
# 2) 安装依赖（二选一）
pip install unsloth "trl>=0.12" datasets
pip install "transformers>=4.51" "peft>=0.13" "trl>=0.12" datasets accelerate bitsandbytes
# 3) 检查数据
python finetune/train_lora.py --dry-run
# 4) 训练
HF_HUB_OFFLINE=1 python finetune/train_lora.py --base /models/Qwen3.5-4B --backend unsloth
```

## 导出到本机 Ollama

```bash
BASE=/models/Qwen3.5-4B LLAMA_CPP=/opt/llama.cpp bash finetune/export_to_ollama.sh
IRDS_LLM_MODEL=irds-advisor:4b .venv/bin/python scripts/run_advisor_eval.py --label lora   # 与基线对比
```

导出脚本沿用当前 `qwen3.5:4b` 的 chat 模板与停止符，只替换权重。评测通过（准确率不低于当前基线、拒答和引用不退化）后，再把 `config/ai_advisor.yaml` 的 `chat_model` 改为 `irds-advisor:4b`。

## 注意

- 数据由规则和公开统计模板化生成，覆盖“怎么答”的格式与纪律；真实业务问法要等组员整理后补充（追加到 `build_sft_dataset.py` 或另放 `data/extra_*.jsonl`）。
- 知识库更新后先重新生成数据再训练，否则模型会记住旧口径；线上仍以检索到的片段为准。
- 训练数据只含公开统计与项目规则，不含渤海财险的任何业务数据。
