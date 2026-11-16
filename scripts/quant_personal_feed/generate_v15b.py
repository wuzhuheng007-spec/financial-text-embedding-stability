"""One isolated, hard-killable local Qwen generation. Private request and response only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .analysis_contract_v15b import messages

EXPECTED_REVISION = 'aa8e72537993ba99e69dfaafa59ed015b17504d1'
EXPECTED_CONFIG = 'eed00b17e22553979d090fa492e587e92885e328914c8e0b0b78f0a0d3576b3b'
EXPECTED_TOKENIZER = 'c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539'


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--response',type=Path,required=True)
    args=parser.parse_args()
    req=json.loads(args.request.read_text(encoding='utf-8'))
    model_dir=Path(os.environ['QUANT_V15_MODEL_DIR'])
    if model_dir.name != EXPECTED_REVISION or req['model_revision'] != EXPECTED_REVISION:
        raise RuntimeError('PINNED_REVISION_MISMATCH')
    for filename, expected in [('config.json',EXPECTED_CONFIG),('tokenizer.json',EXPECTED_TOKENIZER)]:
        if hashlib.sha256((model_dir/filename).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('MODEL_FILE_HASH_MISMATCH')
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE'):
        os.environ[key]='1'
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA_UNAVAILABLE')
    torch.manual_seed(req['seed']); torch.cuda.manual_seed_all(req['seed'])
    tok=AutoTokenizer.from_pretrained(str(model_dir),local_files_only=True,trust_remote_code=False)
    model=AutoModelForCausalLM.from_pretrained(str(model_dir),local_files_only=True,
                trust_remote_code=False,torch_dtype=torch.float16,attn_implementation='eager').to('cuda')
    model.eval()
    loaded=args.request.with_suffix('.loaded')
    loaded.write_text('loaded\n',encoding='ascii')
    prompt=tok.apply_chat_template(messages(req['source_text'],repair_response=req.get('repair_response')),
                                   tokenize=False,add_generation_prompt=True)
    inputs=tok(prompt,return_tensors='pt',add_special_tokens=False).input_ids.to('cuda')
    if inputs.shape[1]>req['max_input_tokens']:
        args.response.write_text(json.dumps({'error':'INPUT_TOKEN_BUDGET','input_tokens':int(inputs.shape[1])}),encoding='utf-8')
        return
    with torch.inference_mode():
        output=model.generate(inputs,do_sample=False,num_beams=1,max_new_tokens=req['max_new_tokens'],
                 pad_token_id=tok.eos_token_id,use_cache=True)
    torch.cuda.synchronize()
    generated=output[0][inputs.shape[1]:]
    raw=tok.decode(generated,skip_special_tokens=True,clean_up_tokenization_spaces=False)
    args.response.write_text(json.dumps({'raw':raw,'generated_tokens':int(generated.shape[0]),
          'input_tokens':int(inputs.shape[1]),'device':'cuda','dtype':'float16'},ensure_ascii=False),encoding='utf-8')


if __name__=='__main__': main()
