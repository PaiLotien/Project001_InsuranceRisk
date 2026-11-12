#!/usr/bin/env bash
# 把 LoRA 适配器合并进基础模型 → 转 GGUF → 量化 Q4_K_M → 注册为本机 Ollama 模型。全程离线。
#
# 依赖：llama.cpp 源码目录（含 convert_hf_to_gguf.py 和已编译的 llama-quantize），
#       Python 包 transformers、peft、torch（CPU 即可完成合并，约需 16 GB 内存）。
# 用法：
#   BASE=/models/Qwen3.5-4B LLAMA_CPP=/opt/llama.cpp bash finetune/export_to_ollama.sh
# 完成后把 config/ai_advisor.yaml 的 chat_model 改为 irds-advisor:4b（或设置环境变量 IRDS_LLM_MODEL）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="${BASE:?请设置 BASE=基础模型本地目录}"
LLAMA_CPP="${LLAMA_CPP:?请设置 LLAMA_CPP=llama.cpp 目录}"
ADAPTER="${ADAPTER:-$HERE/outputs/irds-qwen35-4b-lora}"
WORK="${WORK:-$HERE/outputs/export}"
NAME="${NAME:-irds-advisor:4b}"
QUANT="${QUANT:-Q4_K_M}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p "$WORK"

echo "[1/4] 合并 LoRA → $WORK/merged"
python - "$BASE" "$ADAPTER" "$WORK/merged" <<'PY'
import sys, torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base, adapter, out = sys.argv[1:]
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cpu")
model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
model.save_pretrained(out, safe_serialization=True)
AutoTokenizer.from_pretrained(base).save_pretrained(out)
PY

echo "[2/4] 转 GGUF（F16）"
python "$LLAMA_CPP/convert_hf_to_gguf.py" "$WORK/merged" --outtype f16 --outfile "$WORK/irds-advisor-f16.gguf"

echo "[3/4] 量化 $QUANT"
QBIN="$(ls "$LLAMA_CPP"/build/bin/llama-quantize "$LLAMA_CPP"/llama-quantize 2>/dev/null | head -1)"
"$QBIN" "$WORK/irds-advisor-f16.gguf" "$WORK/irds-advisor-$QUANT.gguf" "$QUANT"

echo "[4/4] 注册 Ollama 模型 $NAME"
# 沿用当前 qwen3.5:4b 的 chat 模板与停止符，只替换权重
ollama show qwen3.5:4b --modelfile | grep -v '^FROM ' > "$WORK/Modelfile.tmp"
{ echo "FROM $WORK/irds-advisor-$QUANT.gguf"; cat "$WORK/Modelfile.tmp"; } > "$WORK/Modelfile"
ollama create "$NAME" -f "$WORK/Modelfile"
echo "完成。验证：IRDS_LLM_MODEL=$NAME .venv/bin/python scripts/run_advisor_eval.py --label lora"
