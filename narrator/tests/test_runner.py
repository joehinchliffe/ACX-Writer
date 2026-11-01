"""RunPod runner flow with a fake API and fake SSH: rejects, fallback, ordering."""
import argparse
import json
from pathlib import Path

import pytest

import runpod_run as rr


class FakeAPI:
    def __init__(self):
        self.created, self.terminated, self.events = [], [], []

    def create(self, name, gpu, cloud, image, disk, pub):
        pid = f"pod{len(self.created)}"
        self.created.append((pid, gpu, cloud))
        self.events.append(("create", pid, cloud))
        return {"id": pid, "costPerHr": 0.34}

    def get(self, pid):
        return {"id": pid, "publicIp": "1.2.3.4", "portMappings": {"22": 22022}, "costPerHr": 0.34}

    def terminate(self, pid):
        self.terminated.append(pid)
        self.events.append(("terminate", pid))


class FakeRemote:
    """bootstrap exit codes per pod: busy GPU, slow net, ..., then OK."""
    codes = {}

    def __init__(self, api, out):
        self.api, self.out, self.pod = api, out, None

    def __call__(self, ip, port):
        self.pod = self.api.created[-1][0]
        return self

    def wait(self):
        return True

    def push(self, *a, **k):
        pass

    def run(self, cmd):
        if "bootstrap" in cmd:
            return FakeRemote.codes.get(self.pod, 0)
        return 0

    def pull(self, src, dst, excludes=()):
        self.api.events.append(("pull", self.pod))
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "session.json").write_text(json.dumps({"books": [{
            "book": "b", "finished_audio_min": 300.0, "raw_xrt_first_pass": 61.0, "end_to_end_xrt": 31.0,
            "redos": {"chunks_redone": 4, "takes": 5, "still_failing": []}}]}))


def args_for(tmp_path, book):
    key = tmp_path / "k"
    Path(str(key) + ".pub").write_text("ssh-ed25519 AAAA test")
    return argparse.Namespace(books=[str(book)], out=str(tmp_path / "out"), gpus=["4090", "3090"],
                              max_rejects=3, max_attempts=10, image="img", disk_gb=120, batch=96,
                              ssh_key=key, keep_work=False, job_args="")


def test_rejects_then_secure_and_terminates_before_upload(tmp_path):
    book = tmp_path / "b"
    book.mkdir()
    (book / "book.json").write_text("{}")
    api = FakeAPI()
    FakeRemote.codes = {"pod0": 3, "pod1": 4, "pod2": 3}     # three community rejects
    remote = FakeRemote(api, tmp_path)
    uploaded = []
    s = rr.run_session(args_for(tmp_path, book), api, remote,
                       after=lambda: uploaded.append(list(api.events)), sleep=lambda s: None)
    clouds = [c for _, _, c in api.created]
    assert clouds == ["COMMUNITY"] * 3 + ["SECURE"]
    assert api.terminated == ["pod0", "pod1", "pod2", "pod3"]
    ev = uploaded[0]
    assert ev.index(("pull", "pod3")) < ev.index(("terminate", "pod3"))   # results home, then terminate
    assert ev[-1] == ("terminate", "pod3")                                # upload only after that
    assert [r["reason"] for r in s["rejected_pods"]] == ["GPU already busy", "slow download", "GPU already busy"]
    assert s["finished_audio_hours"] == 5.0 and s["usd_per_finished_hour"] >= 0
    assert (tmp_path / "out" / "runs.jsonl").exists()


def test_pod_terminated_when_job_crashes(tmp_path):
    book = tmp_path / "b"
    book.mkdir()
    (book / "book.json").write_text("{}")
    api = FakeAPI()
    FakeRemote.codes = {"pod0": 1}                             # bootstrap error (not a reject)
    with pytest.raises(RuntimeError):
        rr.run_session(args_for(tmp_path, book), api, FakeRemote(api, tmp_path), sleep=lambda s: None)
    assert api.terminated == ["pod0"]
