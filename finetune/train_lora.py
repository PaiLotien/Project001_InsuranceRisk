"""Qwen3.5-4B LoRA SFT（在带 GPU 的机器上离线运行）。

数据：finetune/data/train*.jsonl / val*.jsonl（chat 格式），system 提示词取 finetune/data/system_prompt.md，
训练时自动插入每条样本最前面，与线上推理的固定 system 前缀一致。只对 assistant 回复计算损失。

两种后端：
  --backend unsloth（默认，显存更省、更快）：pip install "unsloth" "trl>=0.12" datasets
  --backend peft  ：pip install "transformers>=4.51" "peft>=0.13" "trl>=0.12" datasets accelerate bitsandbytes

显存需求（序列长度 2048，batch 2 × 梯度累积 8）：
  QLoRA 4bit（--load-in-4bit，默认）：约 10–12 GB（RTX 3060 12G / 4070 / T4 16G 可跑）
  LoRA bf16（--no-4bit）            ：约 20–24 GB（RTX 3090 / 4090 / A10 24G）
时长参考：约 330 条 × 3 epoch，单张 4090 约 10–15 分钟；T4 约 40–60 分钟。

离线运行：先把基础模型下载到本地目录（--base 指向该目录），并设置 HF_HUB_OFFLINE=1；
训练数据与日志都留在本机，不需要任何云端 API。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_rows(pattern: str, system: str) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(HERE / "data" / pattern))):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            rows.append({"messages": [{"role": "system", "content": system}] + item["messages"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("IRDS_BASE_MODEL", "Qwen/Qwen3.5-4B"),
                    help="基础模型（本地目录或 HF 名称；离线时用本地目录）")
    ap.add_argument("--backend", choices=["unsloth", "peft"], default="unsloth")
    ap.add_argument("--out", default=str(HERE / "outputs/irds-qwen35-4b-lora"))
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--no-4bit", dest="load_in_4bit", action="store_false")
    ap.add_argument("--dry-run", action="store_true", help="只检查数据与 chat 模板，不加载模型")
    a = ap.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    system = (HERE / "data/system_prompt.md").read_text(encoding="utf-8")
    train_rows, val_rows = load_rows("train*.jsonl", system), load_rows("val*.jsonl", system)
    print(f"train={len(train_rows)} val={len(val_rows)}")
    if a.dry_run:
        for r in train_rows[:1]:
            print(json.dumps(r, ensure_ascii=False)[:800])
        return

    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    target = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    if a.backend == "unsloth":
        from unsloth import FastLanguageModel

        model, tokenizer = FastLanguageModel.from_pretrained(
            a.base, max_seq_length=a.max_len, load_in_4bit=a.load_in_4bit, dtype=None)
        model = FastLanguageModel.get_peft_model(
            model, r=a.rank, lora_alpha=a.alpha, lora_dropout=0.0, target_modules=target,
            use_gradient_checkpointing="unsloth", random_state=42)
    else:
        import torch
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        tokenizer = AutoTokenizer.from_pretrained(a.base)
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.bfloat16) if a.load_in_4bit else None
        model = AutoModelForCausalLM.from_pretrained(a.base, quantization_config=quant, torch_dtype=torch.bfloat16,
                                                     device_map="auto")
        if a.load_in_4bit:
            model = prepare_model_for_kbit_training(model)
        model = get_peft_model(model, LoraConfig(r=a.rank, lora_alpha=a.alpha, lora_dropout=0.05,
                                                 target_modules=target, task_type="CAUSAL_LM"))

    def render(row):
        # enable_thinking=False：线上用 think=false，训练模板保持一致
        try:
            text = tokenizer.apply_chat_template(row["messages"], tokenize=False, enable_thinking=False)
        except TypeError:
            text = tokenizer.apply_chat_template(row["messages"], tokenize=False)
        return {"text": text}

    train_ds = Dataset.from_list(train_rows).map(render, remove_columns=["messages"])
    val_ds = Dataset.from_list(val_rows).map(render, remove_columns=["messages"])
    common = dict(
        output_dir=a.out, num_train_epochs=a.epochs, learning_rate=a.lr, lr_scheduler_type="cosine",
        warmup_ratio=0.05, per_device_train_batch_size=a.batch, gradient_accumulation_steps=a.grad_accum,
        per_device_eval_batch_size=a.batch, eval_strategy="epoch", save_strategy="epoch", logging_steps=5,
        dataset_text_field="text", bf16=True, report_to="none", seed=42,
    )
    # trl 不同版本的参数名不同：max_length / max_seq_length，processing_class / tokenizer
    try:
        cfg = SFTConfig(max_length=a.max_len, **common)
    except TypeError:
        cfg = SFTConfig(max_seq_length=a.max_len, **common)
    try:
        trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=train_ds, eval_dataset=val_ds, args=cfg)
    except TypeError:
        trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=train_ds, eval_dataset=val_ds, args=cfg)
    if a.backend == "unsloth":
        # 只对 assistant 回复计算损失
        from unsloth.chat_templates import train_on_responses_only

        trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                          response_part="<|im_start|>assistant\n")
    trainer.train()
    model.save_pretrained(a.out)
    tokenizer.save_pretrained(a.out)
    print("LoRA adapter saved to", a.out)


if __name__ == "__main__":
    main()
