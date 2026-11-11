"""Job templates: one-click starting points for Meridian Compute.

A student should not have to write `docker.io/x@sha256:...` plus torchrun flags
to run a first job. A template is a static, reviewed set of defaults for the
job form (image, arguments, GPUs, replicas, timeout). Submitting a job with
`template: "<id>"` starts from these values and any field in the request
overrides them; the merged spec still goes through the normal job validation.

Every image is pinned by digest, because that is what Compute accepts. The
comment beside each digest records the tag it was resolved from, so it can be
bumped later (see "Templates" in docs/COMPUTE.md). Digests are the multi-arch
index where the publisher ships one; `pytorch/pytorch` CUDA images are
published for linux/amd64 only, so their digest is that image's manifest.

Arguments may use the same `{{rank}} {{replicas}} {{port}} {{peers}} {{master}}
{{job_id}} {{service_token}}` placeholders as any other job.
"""

from __future__ import annotations

import copy
from typing import Any

from .service import ControlServiceError

# docker.io/pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime (linux/amd64 only)
PYTORCH_IMAGE = "docker.io/pytorch/pytorch@sha256:77f17f843507062875ce8be2a6f76aa6aa3df7f9ef1e31d9d7432f4b0f563dee"
# quay.io/jupyter/pytorch-notebook:cuda12-2026-11-10 (index: linux/amd64, linux/arm64)
JUPYTER_IMAGE = (
    "quay.io/jupyter/pytorch-notebook@sha256:28b86de74ebb2175453008c1f92db891656fee0a384bc81afb7f70506eceb4ae"
)
# docker.io/vllm/vllm-openai:v0.19.1 (index: linux/amd64, linux/arm64)
VLLM_IMAGE = "docker.io/vllm/vllm-openai@sha256:2622f38a0aa646c15ccc27bd5033911a58fd94ac69fd8f86aba0692d77cfe5b9"

# Runs under `torchrun --no-python`, so it needs no file in the image. Every replica
# joins the group, all-reduces its rank + 1 and prints what it saw: with N replicas
# the sum must be N(N+1)/2. Replace it with your own script.
_DDP_SCRIPT = (
    "import os, torch, torch.distributed as dist\n"
    "gpu = torch.cuda.is_available()\n"
    "if gpu:\n"
    "    torch.cuda.set_device(int(os.environ.get('LOCAL_RANK', '0')))\n"
    "dist.init_process_group('nccl' if gpu else 'gloo')\n"
    "rank, world = dist.get_rank(), dist.get_world_size()\n"
    "t = torch.tensor([rank + 1.0], device='cuda' if gpu else 'cpu')\n"
    "dist.all_reduce(t)\n"
    "where = torch.cuda.get_device_name(0) if gpu else 'cpu'\n"
    "print(f'rank {rank} of {world}: all_reduce sum={t.item():.0f} (expected {world * (world + 1) // 2}) on {where}', "
    "flush=True)\n"
    "dist.destroy_process_group()\n"
)

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "jupyterlab",
        "title": "JupyterLab",
        "summary": "A JupyterLab notebook server with PyTorch and CUDA, protected by a token.",
        "kind": "service",
        "image": JUPYTER_IMAGE,
        "args": [
            "start-notebook.py",
            "--ServerApp.ip=0.0.0.0",
            "--ServerApp.port={{port}}",
            "--IdentityProvider.token={{service_token}}",
        ],
        "env": {},
        "gpus_per_replica": 1,
        "replicas": 1,
        "timeout_seconds": 28800,
        "docs": (
            "When the job is running, open its page and use the notebook link shown under Service: "
            "it is <endpoint>/lab?token=<token>. Files live inside the container and are lost when "
            "the job ends, so download your notebooks or save them to your own storage."
        ),
    },
    {
        "id": "pytorch-ddp",
        "title": "PyTorch distributed (2 nodes)",
        "summary": "Two replicas join one torchrun group and all-reduce, to check multi-node training works.",
        "kind": "job",
        "image": PYTORCH_IMAGE,
        "args": [
            "torchrun",
            "--nnodes={{replicas}}",
            "--nproc-per-node=1",
            "--node-rank={{rank}}",
            "--rdzv-backend=c10d",
            "--rdzv-endpoint={{master}}",
            "--rdzv-id={{job_id}}",
            "--no-python",
            "python",
            "-c",
            _DDP_SCRIPT,
        ],
        "env": {"PYTHONUNBUFFERED": "1"},
        "gpus_per_replica": 1,
        "replicas": 2,
        "timeout_seconds": 900,
        "docs": (
            "Each replica prints its rank, the group size and the all-reduce result; with two replicas "
            "the sum is 3. The built-in script is only a smoke test: to train, replace the last two "
            "arguments (`-c` and the script) with your own command, for example `python train.py`."
        ),
    },
    {
        "id": "vllm-server",
        "title": "vLLM OpenAI-compatible server",
        "summary": "Serves a small open model (Qwen2.5-1.5B-Instruct) over an OpenAI-compatible API on one GPU.",
        "kind": "service",
        "image": VLLM_IMAGE,
        "args": [
            "--model", "Qwen/Qwen2.5-1.5B-Instruct",
            "--host", "0.0.0.0",
            "--port", "{{port}}",
            "--api-key", "{{service_token}}",
        ],
        "env": {},
        "gpus_per_replica": 1,
        "replicas": 1,
        "timeout_seconds": 28800,
        "docs": (
            "The node downloads the model from Hugging Face on start, so the first start takes a few "
            "minutes. Then call <endpoint>/v1/chat/completions with the header "
            "`Authorization: Bearer <token>`, or add the endpoint to a Meridian gateway as a backend. "
            "Change the `--model` value for another model that fits your GPU."
        ),
    },
    {
        "id": "python-script",
        "title": "Python hello world",
        "summary": "Prints the name of the GPU the job was given: the quickest check that everything works.",
        "kind": "job",
        "image": PYTORCH_IMAGE,
        "args": ["python", "-c", "import torch; print(torch.cuda.get_device_name(0))"],
        "env": {"PYTHONUNBUFFERED": "1"},
        "gpus_per_replica": 1,
        "replicas": 1,
        "timeout_seconds": 900,
        "docs": (
            "The job's page shows the GPU name in the replica's output when it finishes. Replace the "
            "arguments with your own command, for example `python train.py`."
        ),
    },
]

_BY_ID = {t["id"]: t for t in TEMPLATES}
# The template fields that are defaults for a job; the rest is presentation.
_JOB_FIELDS = ("kind", "image", "args", "env", "gpus_per_replica", "replicas", "timeout_seconds")


def list_templates() -> list[dict[str, Any]]:
    return copy.deepcopy(TEMPLATES)


def apply_template(body: dict) -> dict:
    """The job body with its template's fields as defaults. Fields in `body` win."""
    tid = body.get("template")
    if tid is None:
        return body
    template = _BY_ID.get(tid) if isinstance(tid, str) else None
    if template is None:
        known = ", ".join(_BY_ID)
        raise ControlServiceError("UNKNOWN_TEMPLATE", f"unknown template {tid!r}; use one of: {known}", http_status=400)
    merged = {k: copy.deepcopy(template[k]) for k in _JOB_FIELDS}
    merged.update({k: v for k, v in body.items() if k != "template"})
    return merged
