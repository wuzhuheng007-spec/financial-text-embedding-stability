#!/bin/bash
# Classify the trial work file with the production profile on the local GPU. Results stay in this private folder;
# they are never delivered to the server inbox.
P=/c/Users/ROG/Documents/ChatGPT/quant/local_only/x_like5_trial_20261127
W=C:/Users/ROG/Documents/ChatGPT/quant/worktrees/x_stance_r3_page_20261126
M="E:/quant_news_first_inference_runtime_20261114/model_cache/models--Qwen--Qwen2.5-3B-Instruct/snapshots/aa8e72537993ba99e69dfaafa59ed015b17504d1"
cd $W; s=$(date +%s); echo "start $(date -u +%FT%TZ)"
PYTHONPATH=$W E:/quant_news_first_inference_runtime_20261114/venv/Scripts/python.exe -m scripts.x_stance_v1.local_gpu work --model "$M" --work "C:/Users/ROG/Documents/ChatGPT/quant/local_only/x_like5_trial_20261127/work.jsonl" --results "C:/Users/ROG/Documents/ChatGPT/quant/local_only/x_like5_trial_20261127/results.jsonl" --profile simple-v1-qwen25-3b-local-gpu > $P/run.out 2> $P/run.err
echo "exit=$? seconds=$(( $(date +%s) - s )) lines=$(wc -l < $P/results.jsonl 2>/dev/null) $(date -u +%FT%TZ)"; echo GPU_DONE
