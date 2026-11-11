# ruff: noqa: F811
"""Job templates: the list endpoint, submitting from a template, override
precedence, and that every shipped template is a valid, digest-pinned job."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_cloud import CSRF, login, workspace
from test_compute import IMAGE, FakeNode, client, clock, world  # noqa: F401

from meridian_control.compute import IMAGE_REF, ComputeService
from meridian_control.service import ControlServiceError
from meridian_control.templates import TEMPLATES, apply_template, list_templates

IDS = ["jupyterlab", "pytorch-ddp", "vllm-server", "python-script"]
FIELDS = {"id", "title", "summary", "kind", "image", "args", "env", "gpus_per_replica", "replicas",
          "timeout_seconds", "docs"}


def _post(c, ws, **body):
    return c.post(f"/api/w/{ws}/jobs", headers=CSRF, json=body)


def test_the_four_templates_are_listed_for_viewers(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    owner.post(f"/api/w/{ws}/invitations", headers=CSRF, json={"email": "viewer@iit.ac.in", "role": "viewer"})
    viewer = login(client, tmp_path, "viewer@iit.ac.in", clock)
    for c in (owner, viewer):
        r = c.get(f"/api/w/{ws}/job-templates")
        assert r.status_code == 200
        listed = r.json()["templates"]
        assert [t["id"] for t in listed] == IDS
        assert all(set(t) == FIELDS for t in listed)
    assert TestClient(client.app).get(f"/api/w/{ws}/job-templates").status_code == 401


def test_every_template_is_pinned_by_digest_and_passes_job_validation(client):
    comp: ComputeService = client.app.state.compute
    assert [t["id"] for t in TEMPLATES] == IDS
    for t in list_templates():
        assert IMAGE_REF.match(t["image"]), t["id"]
        assert "@sha256:" in t["image"] and t["image"].split("/")[0] in ("docker.io", "quay.io")
        assert t["summary"].endswith(".") and t["docs"]
        spec = comp._validate_job(apply_template({"template": t["id"]}))
        assert (spec["kind"], spec["image"], spec["args"], spec["env"]) == (t["kind"], t["image"], t["args"], t["env"])
        assert spec["replicas"] == t["replicas"] and spec["gpus_per_replica"] == t["gpus_per_replica"]
        assert spec["timeout_seconds"] == t["timeout_seconds"]


def test_services_listen_on_the_assigned_port_and_require_the_token():
    by_id = {t["id"]: t for t in TEMPLATES}
    jupyter = " ".join(by_id["jupyterlab"]["args"])
    assert "--ServerApp.port={{port}}" in jupyter and "--ServerApp.ip=0.0.0.0" in jupyter
    assert "--IdentityProvider.token={{service_token}}" in jupyter
    vllm = by_id["vllm-server"]["args"]
    assert vllm[vllm.index("--port") + 1] == "{{port}}" and vllm[vllm.index("--host") + 1] == "0.0.0.0"
    assert vllm[vllm.index("--api-key") + 1] == "{{service_token}}"
    ddp = by_id["pytorch-ddp"]["args"]
    assert "--nnodes={{replicas}}" in ddp and "--node-rank={{rank}}" in ddp
    assert "--rdzv-backend=c10d" in ddp and "--rdzv-endpoint={{master}}" in ddp
    assert (by_id["pytorch-ddp"]["replicas"], by_id["pytorch-ddp"]["gpus_per_replica"]) == (2, 1)
    assert by_id["python-script"]["args"][:2] == ["python", "-c"]


def test_submitting_a_template_runs_it_with_its_defaults(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    node = FakeNode(client.app, owner, ws, gpus=1, host="203.0.113.7")
    r = _post(owner, ws, template="jupyterlab", name="notebook")
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["kind"] == "service" and job["image"] == TEMPLATES[0]["image"] and job["timeout_seconds"] == 28800
    spec = node.sync()["jobs"][0]
    token = job["service"]["token"]
    assert spec["port"] == 9000 and spec["kind"] == "service"
    assert spec["args"] == ["start-notebook.py", "--ServerApp.ip=0.0.0.0", "--ServerApp.port=9000",
                            f"--IdentityProvider.token={token}"]


def test_a_two_node_template_job_gets_rank_and_the_master_endpoint(client, tmp_path, clock):
    owner = login(client, tmp_path, "hpc@iisc.ac.in", clock)
    ws = workspace(owner)
    a = FakeNode(client.app, owner, ws, gpus=1, host="10.0.0.1", name="a")
    b = FakeNode(client.app, owner, ws, gpus=1, host="10.0.0.2", name="b")
    r = _post(owner, ws, template="pytorch-ddp")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "scheduled"
    r0, r1 = a.sync()["jobs"][0], b.sync()["jobs"][0]
    for spec, rank in ((r0, 0), (r1, 1)):
        assert spec["args"][:7] == ["torchrun", "--nnodes=2", "--nproc-per-node=1", f"--node-rank={rank}",
                                    "--rdzv-backend=c10d", "--rdzv-endpoint=10.0.0.1:9000",
                                    f"--rdzv-id={r.json()['id']}"]
        assert "{{" not in " ".join(spec["args"])
        assert spec["env"]["PYTHONUNBUFFERED"] == "1"


def test_request_fields_override_the_template_and_the_rest_still_apply(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    FakeNode(client.app, owner, ws, gpus=2)
    r = _post(owner, ws, template="python-script", args=["python", "train.py"], gpus_per_replica=2,
              env={"SEED": "7"}, timeout_seconds=60, name="mine")
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["args"] == ["python", "train.py"] and job["gpus_per_replica"] == 2
    assert job["env"] == {"SEED": "7"} and job["timeout_seconds"] == 60 and job["name"] == "mine"
    assert job["image"] == TEMPLATES[3]["image"] and job["kind"] == "job"  # untouched fields come from the template

    # The image can be overridden too, and is still validated.
    ok = _post(owner, ws, template="python-script", image=IMAGE)
    assert ok.status_code == 201 and ok.json()["image"] == IMAGE
    bad = _post(owner, ws, template="python-script", image="pytorch/pytorch:latest")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_IMAGE"
    # Validation applies to the merged spec: a service cannot have two replicas.
    assert _post(owner, ws, template="vllm-server", replicas=2).status_code == 400


@pytest.mark.parametrize("bad", ["nope", "", 7, ["jupyterlab"], {"id": "jupyterlab"}])
def test_an_unknown_template_is_a_400_and_creates_nothing(client, tmp_path, clock, bad):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    r = _post(owner, ws, template=bad, image=IMAGE)
    assert r.status_code == 400 and r.json()["error"]["code"] == "UNKNOWN_TEMPLATE"
    assert "jupyterlab" in r.json()["error"]["message"]
    assert owner.get(f"/api/w/{ws}/jobs").json()["jobs"] == []


def test_apply_template_leaves_a_plain_job_alone_and_never_mutates_the_catalogue():
    body = {"image": IMAGE}
    assert apply_template(body) is body
    merged = apply_template({"template": "python-script"})
    merged["args"].append("x")
    merged["env"]["A"] = "b"
    assert TEMPLATES[3]["args"][-1] != "x" and "A" not in TEMPLATES[3]["env"]
    with pytest.raises(ControlServiceError):
        apply_template({"template": "missing"})


def test_cli_lists_templates_and_runs_one_without_an_image(world, monkeypatch, capsys):
    from meridian.cli.compute import main

    key = world.consumer.post(
        f"/api/w/{world.cws}/api-keys", headers=CSRF, json={"name": "laptop", "role": "admin"}
    ).json()["token"]
    monkeypatch.setenv("MERIDIAN_WORKSPACE", world.cws)
    monkeypatch.setenv("MERIDIAN_API_KEY", key)
    http = TestClient(world.app)
    lines: list[str] = []

    def run(*argv, sleep=lambda _s: None):
        lines.clear()
        return main(list(argv), client=http, out=lines.append, sleep=sleep)

    assert run("templates") == 0
    assert lines[0].split()[:2] == ["ID", "TYPE"]
    assert [ln.split()[0] for ln in lines[1:5]] == IDS and "meridian compute run --template" in lines[-1]

    # --template replaces --image, and a flag overrides the template.
    assert run("run", "--template", "python-script", "--rental", world.rent()["id"], "--timeout", "120") == 0
    assert lines[0].startswith("submitted job_")
    job = world.consumer.get(f"/api/w/{world.cws}/jobs/{lines[0].split()[1]}").json()
    assert job["image"] == TEMPLATES[3]["image"] and job["timeout_seconds"] == 120 and job["gpus_per_replica"] == 1
    detail = world.consumer.get(f"/api/w/{world.cws}/jobs/{job['id']}").json()
    assert detail["args"] == TEMPLATES[3]["args"]

    # A command after `--` replaces the template's arguments.
    assert run("run", "--template", "python-script", "--", "python", "train.py") == 0
    latest = world.consumer.get(f"/api/w/{world.cws}/jobs/{lines[0].split()[1]}").json()
    assert latest["args"] == ["python", "train.py"]

    # Neither --image nor --template is an error; an unknown template is reported.
    assert run("run", "--", "python", "x.py") == 1
    assert "--template" in capsys.readouterr().err
    assert run("run", "--template", "nope") == 1
    assert "unknown template" in capsys.readouterr().err
