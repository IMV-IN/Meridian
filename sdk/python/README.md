# meridian-compute

Run a Python function on a GPU in your institute's Meridian Compute cloud, from your own script or laptop.

`meridian-compute` is the Python client for **Meridian Compute tasks**. You decorate a function, and
`.run()` ships your project's code and the call arguments to a GPU node in your Meridian workspace,
runs the function there, and returns its result. It is phase 1 of the feature: one function call, one
GPU node, JSON in and JSON out. No dependencies are installed remotely.

Status: alpha (`0.1.0a1`). It needs a Meridian control plane with tasks switched on.

## Install

```bash
pip install --pre meridian-compute
```

Python 3.10 or newer. The only dependency is `httpx`.

## Get a workspace URL and API key

1. Open your Meridian Cloud console and sign in. The address you use is the **URL**.
2. Pick your workspace. Its id, `ws_` followed by 20 hex digits, is the **workspace**.
3. Go to Settings, API keys, and create a key with the operator role. It starts with `mck_` and is
   shown once. Keep it secret.

Then run `meridian-compute login`, which asks for the three values (the key is not echoed) and stores
them in `~/.config/meridian-compute/credentials.json`, readable only by you. Or set `MERIDIAN_CLOUD_URL`,
`MERIDIAN_WORKSPACE` and `MERIDIAN_API_KEY`.

## Example

```python
# hello.py
import meridian_compute as mc

project = mc.Project("demo")


@project.task(gpu=1, timeout="30m")
def gpu_name(prefix):
    import torch  # already in the image, not in your laptop's environment

    return prefix + torch.cuda.get_device_name(0)


if __name__ == "__main__":
    print(gpu_name.run("running on "))   # runs remotely and waits
    print(gpu_name("local: "))           # a plain local call, no network
```

Or from the shell: `meridian-compute run hello.py::gpu_name --arg prefix=hi`.

Other handles: `gpu_name.submit(...)` returns a `Run` (`.status()`, `.logs()`, `.cancel()`,
`.result(timeout=...)`, `Run.attach(id)`).

## Limits (phase 1)

- Arguments and results are **JSON only** (plus `bytes` and `nan`/`inf`): no pickle, no numpy arrays,
  no dataclasses. Convert them first, for example `float(x)` or `x.tolist()`.
- Arguments up to 256 KiB, results up to 1 MiB, bundle up to 20 MiB compressed (64 MiB unpacked).
- One function call on one node. Timeout 1 second to 30 days. Up to 16 GPUs.
- Only the top-level package (or single module file) of the task and the paths you list in `include=[...]`
  are uploaded. Nothing is installed remotely: the image's packages are what you get.
- Secrets (`.env`, keys, `.git`, `.ssh`, and similar) are never uploaded, even if you ask.
- Logs are the last 8 KiB of output. No streaming, no retries, no calling a task from a task.

Read the full guide in the Meridian docs (SDK page).

## License

MIT
