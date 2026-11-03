"""Build project images without a Docker socket. Requires kubectl and a local cluster.

The local registry is for a single-node Docker Desktop learning cluster only.
The build itself uses no Docker credentials, host paths or privileged containers.
The separate local image importer uses the node's containerd socket.
"""
from pathlib import Path
import argparse
import datetime as dt
import json
import subprocess
import sys
import tarfile
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".run"
NS = "volleyball-build"
PREFIX = "localhost:32000/volleyball-team-"


def kube(*args, input_data=None, check=True):
    cmd = ["kubectl", "--context", "docker-desktop", "--cache-dir", str(RUN / "kube-cache"), *args]
    result = subprocess.run(cmd, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def apply(objects):
    return kube("apply", "-f", "-", input_data=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))


def registry():
    apply([
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}},
        {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": "registry-data", "namespace": NS},
         "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "5Gi"}}}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "registry", "namespace": NS},
         "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": "build-registry"}},
                  "template": {"metadata": {"labels": {"app": "build-registry"}}, "spec": {
                      "automountServiceAccountToken": False,
                      "containers": [{"name": "registry", "image": "registry:2.8.3", "ports": [{"containerPort": 5000}],
                                      "env": [{"name": "REGISTRY_LOG_LEVEL", "value": "warn"}],
                                      "volumeMounts": [{"name": "data", "mountPath": "/var/lib/registry"}],
                                      "readinessProbe": {"httpGet": {"path": "/v2/", "port": 5000}, "periodSeconds": 3},
                                      "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}}}],
                      "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "registry-data"}}]}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "registry", "namespace": NS},
         "spec": {"type": "NodePort", "selector": {"app": "build-registry"},
                  "ports": [{"port": 5000, "targetPort": 5000, "nodePort": 32000}]}}
    ])
    print(kube("-n", NS, "rollout", "status", "deployment/registry", "--timeout=180s").stdout, flush=True)


def archive(source, target):
    excluded = {"node_modules", "dist", ".git", ".venv", "__pycache__", ".idea", ".run", ".build"}
    with tarfile.open(target, "w:gz") as output:
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            if any(part in excluded for part in relative.parts) or item.name.startswith(".env") or item.suffix in {".pyc", ".log"}:
                continue
            if item.is_file() and not item.is_symlink():
                output.add(item, arcname=relative.as_posix(), recursive=False)


def build(name, directory, tag):
    pod = "build-" + name + "-" + uuid.uuid4().hex[:8]
    context_tar = RUN / (pod + ".tar.gz")
    archive(ROOT / directory, context_tar)
    print(f"Building {name} ({context_tar.stat().st_size} bytes of source)", flush=True)
    apply([{"apiVersion": "v1", "kind": "Pod", "metadata": {"name": pod, "namespace": NS}, "spec": {
        "restartPolicy": "Never", "automountServiceAccountToken": False,
        "initContainers": [{"name": "context", "image": "busybox:1.37",
                            "command": ["sh", "-c", "until test -f /workspace/context.ready; do sleep 1; done; mkdir -p /workspace/source; tar -xzf /workspace/context.tar.gz -C /workspace/source"],
                            "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}]}],
        "containers": [{"name": "builder", "image": "ghcr.io/osscontainertools/kaniko:latest",
                        "args": ["--context=dir:///workspace/source", "--dockerfile=/workspace/source/Dockerfile",
                                 f"--destination=registry.{NS}.svc.cluster.local:5000/volleyball-team-{name}:{tag}",
                                 "--insecure", "--cache=true", "--cache-ttl=72h"],
                        "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "2", "memory": "2Gi"}}}],
        "volumes": [{"name": "workspace", "emptyDir": {}}]
    }}])
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        status = json.loads(kube("-n", NS, "get", "pod", pod, "-o", "json").stdout)
        init = status.get("status", {}).get("initContainerStatuses", [])
        if init and "running" in init[0].get("state", {}):
            break
        time.sleep(2)
    else:
        raise RuntimeError(f"Build Pod did not start: {pod}. Check kubectl -n {NS} describe pod {pod}")
    # Relative Windows source avoids the drive-letter colon ambiguity in kubectl cp.
    cmd = ["kubectl", "--context", "docker-desktop", "-n", NS, "cp", context_tar.name,
           f"{pod}:/workspace/context.tar.gz", "-c", "context"]
    subprocess.run(cmd, cwd=RUN, check=True)
    kube("-n", NS, "exec", pod, "-c", "context", "--", "touch", "/workspace/context.ready")
    time.sleep(3)
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        status = json.loads(kube("-n", NS, "get", "pod", pod, "-o", "json").stdout)["status"]
        if status.get("phase") in {"Succeeded", "Failed"}:
            log = kube("-n", NS, "logs", pod, "-c", "builder", check=False)
            print(log.stdout[-2000:], flush=True)
            if status["phase"] != "Succeeded":
                raise RuntimeError(f"Image build failed: {pod}")
            kube("-n", NS, "delete", "pod", pod, "--wait=false")
            context_tar.unlink(missing_ok=True)
            return PREFIX + name + ":" + tag
        time.sleep(4)
    raise RuntimeError(f"Image build timed out: {pod}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="dev-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S"))
    parser.add_argument("--only", choices=["accounts", "team-info", "frontend"])
    args = parser.parse_args()
    RUN.mkdir(exist_ok=True)
    registry()
    built = {}
    for name, directory in [("accounts", "services/accounts"), ("team-info", "services/team_info"), ("frontend", "frontend")]:
        if args.only is None or args.only == name:
            built[name] = build(name, directory, args.tag)
    (RUN / "build.json").write_text(json.dumps({"tag": args.tag, "images": built}, indent=2), encoding="utf-8")
    print("Build complete: " + args.tag)


if __name__ == "__main__":
    main()
