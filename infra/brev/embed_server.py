"""OpenAI-compatible /v1/embeddings for nvidia/llama-nemotron-embed-1b-v2 on one A100 (benchmark volume exceeds
the hosted rate limit). Mean pooling + L2 norm, "query: "/"passage: " prefixes per the model card.
Run (in the vLLM image, GPU 7):  python3 infra/brev/embed_server.py  -> :8100"""
import os

import torch
import torch.nn.functional as F
import uvicorn
from fastapi import FastAPI
from transformers import AutoModel, AutoTokenizer

MODEL = os.getenv("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
model = AutoModel.from_pretrained(MODEL, trust_remote_code=True, torch_dtype=torch.bfloat16).cuda().eval()
app = FastAPI()


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}


@app.post("/v1/embeddings")
def embeddings(req: dict):
    texts = req["input"] if isinstance(req["input"], list) else [req["input"]]
    prefix = "query: " if req.get("input_type") == "query" else "passage: "
    out: list[list[float]] = []
    for i in range(0, len(texts), 128):
        b = tok([prefix + (t or " ") for t in texts[i:i + 128]], padding=True, truncation=True, max_length=512,
                return_tensors="pt").to("cuda")
        with torch.inference_mode():
            h = model(**b).last_hidden_state
        m = b["attention_mask"].unsqueeze(-1).to(h.dtype)
        out += F.normalize(((h * m).sum(1) / m.sum(1)).float(), dim=-1).cpu().tolist()
    return {"object": "list", "model": MODEL, "usage": {"prompt_tokens": 0, "total_tokens": 0},
            "data": [{"object": "embedding", "index": i, "embedding": e} for i, e in enumerate(out)]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8100")))
