// The job templates, as meridian_control/templates.py defines them (id, title, summary, kind, size, time limit,
// docs). tests/control/test_site_templates.py fails if this list drifts from the Python one. `cmd` is what the
// CLI prints; its format is from meridian/cli/compute.py.

export interface SiteTemplate {
  id: string
  title: string
  summary: string
  kind: "job" | "service"
  replicas: number
  gpus_per_replica: number
  timeout_seconds: number
  docs: string
  cmd: string
}

export const TEMPLATES: SiteTemplate[] = [
  {
    id: "jupyterlab",
    title: "JupyterLab",
    summary: "A JupyterLab notebook server with PyTorch and CUDA, protected by a token.",
    kind: "service",
    replicas: 1,
    gpus_per_replica: 1,
    timeout_seconds: 28800,
    docs: "When the job is running, open its page and use the notebook link shown under Service: it is <endpoint>/lab?token=<token>. Files live inside the container and are lost when the job ends, so download your notebooks or save them to your own storage.",
    cmd: "[[p:$]] meridian compute run --template jupyterlab --name notebook\n[[d:submitted job_5d02c91e7ab34f6d1e90 (queued)]]\n[[c:# then open the job's page and use the notebook link:]]\n[[c:# <endpoint>/lab?token=<token>]]",
  },
  {
    id: "pytorch-ddp",
    title: "PyTorch distributed (2 nodes)",
    summary: "Two replicas join one torchrun group and all-reduce, to check multi-node training works.",
    kind: "job",
    replicas: 2,
    gpus_per_replica: 1,
    timeout_seconds: 900,
    docs: "Each replica prints its rank, the group size and the all-reduce result; with two replicas the sum is 3. The built-in script is only a smoke test: to train, replace the last two arguments (`-c` and the script) with your own command, for example `python train.py`.",
    cmd: "[[p:$]] meridian compute run --template pytorch-ddp --name ddp-check --follow\n[[d:submitted job_8c1e44a07b9d3f2a5c61 (queued)]]\n[[ok:succeeded: Succeeded, Succeeded]]\njob_8c1e44a07b9d3f2a5c61  ddp-check  succeeded\n[[d:--- replica 0 on gpu-01: Succeeded exit=0]]\nrank 0 of 2: all_reduce sum=3 (expected 3) on NVIDIA A100-SXM4-80GB\n[[d:--- replica 1 on gpu-02: Succeeded exit=0]]\nrank 1 of 2: all_reduce sum=3 (expected 3) on NVIDIA A100-SXM4-80GB",
  },
  {
    id: "vllm-server",
    title: "vLLM OpenAI-compatible server",
    summary: "Serves a small open model (Qwen2.5-1.5B-Instruct) over an OpenAI-compatible API on one GPU.",
    kind: "service",
    replicas: 1,
    gpus_per_replica: 1,
    timeout_seconds: 28800,
    docs: "The node downloads the model from Hugging Face on start, so the first start takes a few minutes. Then call <endpoint>/v1/chat/completions with the header `Authorization: Bearer <token>`, or add the endpoint to a Meridian gateway as a backend. Change the first argument (the model name) to serve another model that fits your GPU.",
    cmd: '[[p:$]] meridian compute run --template vllm-server --name qwen\n[[d:submitted job_e7b3910c2d4a8f6b0e12 (queued)]]\n[[c:# when it is running:]]\n[[p:$]] curl <endpoint>/v1/models -H "Authorization: Bearer <token>"',
  },
  {
    id: "python-script",
    title: "Python hello world",
    summary: "Prints the name of the GPU the job was given: the quickest check that everything works.",
    kind: "job",
    replicas: 1,
    gpus_per_replica: 1,
    timeout_seconds: 900,
    docs: "The job's page shows the GPU name in the replica's output when it finishes. Replace the arguments with your own command, for example `python train.py`.",
    cmd: "[[p:$]] meridian compute run --template python-script --name hello --follow\n[[d:submitted job_a4f644d8b66925f93e8a (queued)]]\n[[ok:succeeded: Succeeded]]\njob_a4f644d8b66925f93e8a  hello  succeeded\n[[d:--- replica 0 on gpu-01: Succeeded exit=0]]\nNVIDIA A100-SXM4-80GB",
  },
]

/** "Custom" is the console picker's last option: any image, pinned by digest. */
export const CUSTOM = {
  id: "custom",
  title: "Custom",
  summary: "Your own image and command.",
  docs: "The image must be pinned by digest: a tag can change after you submit, a digest cannot. Get it with `docker inspect --format '{{index .RepoDigests 0}}' image:tag`.",
  cmd: "[[p:$]] meridian compute run --follow \\\n    --image docker.io/pytorch/pytorch@sha256:<digest> \\\n    --gpus 1 -e WANDB_MODE=offline -- python train.py --epochs 3",
}

export const limitLabel = (seconds: number) => (seconds >= 3600 ? `after ${seconds / 3600} h` : `after ${seconds / 60} min`)
export const sizeLabel = (t: Pick<SiteTemplate, "replicas" | "gpus_per_replica">) => `${t.replicas} × ${t.gpus_per_replica} GPU`
