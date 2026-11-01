#!/usr/bin/env python3
"""One command: narrate books on a RunPod GPU and bring the MP3s home.

    export RUNPOD_API_KEY=...            # runpod.io -> Settings -> API Keys
    python runpod_run.py books/my-book books/another --out out/

What it does
  1. Creates a pod (community cloud first; Secure Cloud after --max-rejects
     rejected community pods), RTX 4090 then 3090 by default.
  2. Copies this folder and the books over SSH, runs pod/bootstrap.sh, which
     REJECTS the pod if the GPU already shows load or downloads are < 25 MB/s.
  3. Runs all books in one session (boot + model download shared).
  4. Copies the results back, TERMINATES the pod, and only then runs --after
     (e.g. an upload to Drive), so you never pay GPU time for uploads.
  5. Logs x-real-time per book, redo counts and the session cost to
     out/runs.jsonl.

Needs: ssh + rsync locally, and an SSH key (default ~/.ssh/id_ed25519).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
API = "https://rest.runpod.io/v1"
DEFAULT_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
REJECT_CODES = {3: "GPU already busy", 4: "slow download"}


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


# ------------------------------------------------------------------ API
class RunPodAPI:
    def __init__(self, key: str):
        self.key = key

    def _req(self, method: str, path: str, body: Optional[Dict] = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt.strip() else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"RunPod {method} {path}: {e.code} {e.read().decode()[:400]}") from None

    def create(self, name, gpu, cloud, image, disk_gb, public_key) -> Dict:
        body = {"name": name, "imageName": image, "gpuTypeIds": [gpu], "gpuCount": 1,
                "cloudType": cloud, "containerDiskInGb": disk_gb, "volumeInGb": 0,
                "ports": ["22/tcp"], "env": {"PUBLIC_KEY": public_key}}
        if cloud == "COMMUNITY":
            body["supportPublicIp"] = True
        return self._req("POST", "/pods", body)

    def get(self, pod_id: str) -> Dict:
        return self._req("GET", f"/pods/{pod_id}")

    def terminate(self, pod_id: str) -> None:
        self._req("DELETE", f"/pods/{pod_id}")


# ------------------------------------------------------------------ SSH
class Remote:
    def __init__(self, ip: str, port: int, key_path: Path):
        self.ip, self.port, self.key = ip, port, key_path
        self.opts = ["-i", str(key_path), "-p", str(port), "-o", "StrictHostKeyChecking=no",
                     "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                     "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=15"]

    def run(self, cmd: str, check: bool = False) -> int:
        r = subprocess.run(["ssh", *self.opts, f"root@{self.ip}", cmd])
        if check and r.returncode:
            raise RuntimeError(f"remote command failed ({r.returncode}): {cmd}")
        return r.returncode

    def wait(self, timeout: float = 300) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = subprocess.run(["ssh", *self.opts, f"root@{self.ip}", "true"], capture_output=True)
            if r.returncode == 0:
                return True
            time.sleep(5)
        return False

    def _rsh(self):
        return "ssh " + " ".join(shlex.quote(o) for o in self.opts)

    def push(self, src: Path, dst: str, excludes: List[str] = ()):
        ex = sum((["--exclude", e] for e in excludes), [])
        subprocess.run(["rsync", "-az", "--partial", *ex, "-e", self._rsh(), f"{src}/",
                        f"root@{self.ip}:{dst}/"], check=True)

    def pull(self, src: str, dst: Path, excludes: List[str] = ()):
        dst.mkdir(parents=True, exist_ok=True)
        ex = sum((["--exclude", e] for e in excludes), [])
        subprocess.run(["rsync", "-az", "--partial", *ex, "-e", self._rsh(), f"root@{self.ip}:{src}/",
                        f"{dst}/"], check=True)


# --------------------------------------------------------------- runner
class Rejected(Exception):
    pass


def wait_for_ssh_endpoint(api: RunPodAPI, pod_id: str, timeout: float = 600, sleep=time.sleep):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = api.get(pod_id)
        ip = p.get("publicIp")
        ports = p.get("portMappings") or {}
        port = ports.get("22") or ports.get(22)
        if ip and port:
            return p, ip, int(port)
        sleep(10)
    raise Rejected("pod never got a public SSH endpoint")


def run_session(args, api: RunPodAPI, remote_factory: Callable[[str, int], Remote],
                after: Optional[Callable[[], None]] = None, sleep=time.sleep) -> Dict:
    books = [Path(b).resolve() for b in args.books]
    for b in books:
        if not (b / "book.json").exists():
            raise SystemExit(f"{b}: no book.json")
    pub = Path(str(args.ssh_key) + ".pub").read_text().strip()
    rejects: List[Dict] = []
    session: Dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "books": [b.name for b in books]}
    community_rejects = 0
    attempt = 0
    while True:
        if attempt >= args.max_attempts:
            raise SystemExit(f"no usable pod after {attempt} attempts: {rejects}")
        cloud = "COMMUNITY" if community_rejects < args.max_rejects else "SECURE"
        gpu = args.gpus[attempt % len(args.gpus)]
        attempt += 1
        log(f"creating {cloud.lower()} pod with {gpu}")
        try:
            pod = api.create(f"narrate-{int(time.time())}", gpu, cloud, args.image, args.disk_gb, pub)
        except RuntimeError as e:
            log(f"  not available: {e}")
            if cloud == "COMMUNITY":
                community_rejects += 1
            rejects.append({"gpu": gpu, "cloud": cloud, "reason": "create failed"})
            continue
        pod_id = pod["id"]
        t_created = time.time()
        cost_hr = float(pod.get("costPerHr") or pod.get("adjustedCostPerHr") or 0)
        terminated = False

        def terminate():
            nonlocal terminated
            if not terminated:
                try:
                    api.terminate(pod_id)
                    log(f"pod {pod_id} terminated")
                except Exception as e:      # noqa: BLE001
                    log(f"!! could not terminate pod {pod_id}: {e} -- terminate it in the RunPod console")
                terminated = True
        try:
            info, ip, port = wait_for_ssh_endpoint(api, pod_id, sleep=sleep)
            cost_hr = float(info.get("costPerHr") or info.get("adjustedCostPerHr") or cost_hr)
            r = remote_factory(ip, port)
            if not r.wait():
                raise Rejected("ssh never came up")
            log(f"pod {pod_id} up at {ip}:{port} (${cost_hr:.3f}/h); copying code + books")
            r.push(HERE, "/workspace/narrator", excludes=[".venv", "out", "ab", "__pycache__", ".pytest_cache",
                                                       "bench/results"])
            for b in books:
                r.push(b, f"/workspace/books/{b.name}")
            code = r.run("cd /workspace/narrator && bash pod/bootstrap.sh")
            if code in REJECT_CODES:
                raise Rejected(REJECT_CODES[code])
            if code:
                raise RuntimeError(f"bootstrap failed with exit code {code}")
            t_ready = time.time()
            book_args = " ".join(shlex.quote(f"/workspace/books/{b.name}") for b in books)
            job_code = r.run(f"cd /workspace/narrator && .venv/bin/python -m narrate.job {book_args} "
                             f"--out /workspace/out --batch {args.batch} {args.job_args}")
            t_done = time.time()
            log("copying results back")
            try:
                r.pull("/workspace/out", Path(args.out), excludes=[] if args.keep_work else [".work"])
            finally:
                terminate()                          # BEFORE any upload elsewhere
            t_end = time.time()
            hours = (t_end - t_created) / 3600
            session.update({
                "pod": pod_id, "gpu": gpu, "cloud": cloud, "cost_per_hr": cost_hr,
                "boot_and_setup_min": round((t_ready - t_created) / 60, 1),
                "job_min": round((t_done - t_ready) / 60, 1), "billed_min": round(hours * 60, 1),
                "session_cost_usd": round(hours * cost_hr, 3), "rejected_pods": rejects,
                "job_exit_code": job_code})
            sess = Path(args.out) / "session.json"
            if sess.exists():
                s = json.loads(sess.read_text())
                session["per_book"] = s.get("books")
                fin = sum((b.get("finished_audio_min") or 0) for b in s.get("books", []))
                session["finished_audio_hours"] = round(fin / 60, 3)
                if fin:
                    session["usd_per_finished_hour"] = round(session["session_cost_usd"] / (fin / 60), 3)
            with open(Path(args.out) / "runs.jsonl", "a") as f:
                f.write(json.dumps(session) + "\n")
            log(f"session: ${session['session_cost_usd']:.3f} for {session.get('finished_audio_hours', 0)} h of "
                f"audio" + (f" (${session['usd_per_finished_hour']}/h)" if session.get("usd_per_finished_hour")
                           else ""))
            for b in session.get("per_book") or []:
                log(f"  {b['book']}: raw {b['raw_xrt_first_pass']}x, end-to-end {b['end_to_end_xrt']}x, "
                    f"{b['redos']['chunks_redone']} chunks redone, {len(b['redos']['still_failing'])} still failing")
            if job_code:
                raise SystemExit(f"narration job failed (exit {job_code}); partial output is in {args.out}")
            if after:
                after()
            return session
        except Rejected as e:
            log(f"  rejecting pod {pod_id}: {e}")
            rejects.append({"gpu": gpu, "cloud": cloud, "reason": str(e), "pod": pod_id})
            if cloud == "COMMUNITY":
                community_rejects += 1
            terminate()
        except BaseException:
            terminate()
            raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("books", nargs="+")
    ap.add_argument("--out", default="out")
    ap.add_argument("--gpus", nargs="+", default=["NVIDIA GeForce RTX 4090", "NVIDIA GeForce RTX 3090"])
    ap.add_argument("--max-rejects", type=int, default=3, help="community rejects before Secure Cloud")
    ap.add_argument("--max-attempts", type=int, default=10)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--disk-gb", type=int, default=120)
    ap.add_argument("--batch", type=int, default=96)
    ap.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh" / "id_ed25519")
    ap.add_argument("--keep-work", action="store_true", help="also copy back the per-chunk cache")
    ap.add_argument("--job-args", default="", help="extra args for narrate.job")
    ap.add_argument("--after", default=None, help="shell command run AFTER the pod is terminated")
    a = ap.parse_args(argv)
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise SystemExit("set RUNPOD_API_KEY (runpod.io -> Settings -> API Keys)")
    if not Path(str(a.ssh_key) + ".pub").exists():
        raise SystemExit(f"no SSH key at {a.ssh_key}.pub (create one with: ssh-keygen -t ed25519)")
    after = (lambda: subprocess.run(a.after, shell=True, check=True)) if a.after else None
    run_session(a, RunPodAPI(key), lambda ip, port: Remote(ip, port, a.ssh_key), after=after)


if __name__ == "__main__":
    main()
