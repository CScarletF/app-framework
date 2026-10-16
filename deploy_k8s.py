#!/usr/bin/env python3
"""
deploy_k8s.py -- builds, pushes, and deploys this app to Kubernetes.
The Docker Compose flow (setup.py) is a separate, still-supported option;
nothing here changes it.

Everything outside tracked files is created by this script, so no manual
kubectl steps: namespace, ConfigMap, Secret, image pull secret, the
Deployment and Service, and the admin seed Job.

SUBCOMMANDS
    build      docker build the app and web images, tagged by git commit
    push       docker push both images to the registry
    config     namespace + ConfigMap + Secret + pull secret, from .env/k8s.env
    apply      apply the Deployment and Service, wait for the rollout
    seed       run the admin seed Job (creates the .env admin if none exists)
    status     pods, service, and a health check on every node's NodePort
    teardown   delete the namespace and everything in it
    all        build, push, config, apply, seed, status

USAGE
    python deploy_k8s.py all
    python deploy_k8s.py build --allow-dirty      # uncommitted changes, dev only
    python deploy_k8s.py apply --tag 1a2b3c4d5e6f # redeploy / roll back to a tag

CONFIG
    .env       DB_* and auth settings (same file the Compose flow uses).
               DB_PASSWORD, SECRET_KEY, ADMIN_PASSWORD go into a Secret, the
               rest into a ConfigMap. Secrets are sent to kubectl on stdin,
               never on a command line.
    k8s.env    registry, namespace, preferred node, GHCR credentials
               (copy k8s.env.example; gitignored).

SAFETY
    - Refuses to build from a dirty git tree unless --allow-dirty.
    - `apply`, `seed` and `teardown` show the kubectl context and ask for
      confirmation unless --yes is given.
    - Images are tagged by commit, never `latest`.
"""

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

K8S_DIR = PROJECT_ROOT / "core" / "k8s"
BUILD_DIR = PROJECT_ROOT / ".k8s-build"
TAG_FILE = BUILD_DIR / "tag"
ENV_FILE = PROJECT_ROOT / ".env"
K8S_ENV_FILE = PROJECT_ROOT / "k8s.env"

DEPLOYMENT = "app-framework"
SEED_JOB = "seed-admin"
CONFIG_MAP = "app-config"
SECRET = "app-secrets"
PULL_SECRET = "ghcr-pull"
NODE_PORT = 30080  # must match core/k8s/base/service.yaml

# Placeholders used in core/k8s/base and core/k8s/jobs; the overlay rewrites them.
APP_IMAGE_PLACEHOLDER = "app-framework-app"
WEB_IMAGE_PLACEHOLDER = "app-framework-web"

# .env keys that go into the Secret; everything else goes into the ConfigMap.
SECRET_KEYS = {"DB_PASSWORD", "SECRET_KEY", "ADMIN_PASSWORD"}
REQUIRED_ENV = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]

PREFERRED_NODE_PATH = (
    "/spec/template/spec/affinity/nodeAffinity/"
    "preferredDuringSchedulingIgnoredDuringExecution/0/preference/"
    "matchExpressions/0/values/0"
)

# Auth settings this script makes sure .env carries. Self-contained on
# purpose: the Compose flow (setup.py) and this one are independent options
# and must not import each other.
AUTH_MODULE = "app_user"
MIN_SECRET_LENGTH = 32
ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
AUTH_ENV_DEFAULTS = {
    "SESSION_LIFETIME_MINUTES": "480",
    "SESSION_COOKIE_SECURE": "false",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "admin",
    "LOGIN_MAX_ATTEMPTS": "5",
    "LOGIN_LOCKOUT_MINUTES": "15",
    "PASSWORD_MIN_LENGTH": "8",
}

K8S_DEFAULTS = {
    "K8S_REGISTRY": "",
    "K8S_IMAGE_BASENAME": "app-framework",
    "K8S_NAMESPACE": "app-framework",
    "K8S_PREFERRED_NODE": "jenkins",
    "SOURCE_URL": "",
    "GHCR_USER": "",
    "GHCR_PUSH_TOKEN": "",
    "GHCR_PULL_TOKEN": "",
}


# --- segment: small helpers ----------------------------------------------

def die(message: str):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def require_tools(*names: str):
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        die(f"required tool(s) not found on PATH: {', '.join(missing)}")


def run(cmd: list, input_text: str | None = None, check: bool = True,
        capture: bool = False, env: dict | None = None):
    """Runs a command. The command line is echoed; stdin content never is,
    which is how secrets reach kubectl."""
    print("+ " + " ".join(str(c) for c in cmd))
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=capture,
        env=env,
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        die(f"command failed with exit code {result.returncode}: {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}")
    return result


def read_env_file(path: Path) -> dict:
    """Minimal KEY=VALUE parser: skips blanks and # comments, strips one
    pair of surrounding quotes. No variable expansion."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_settings() -> dict:
    settings = dict(K8S_DEFAULTS)
    settings.update({k: v for k, v in read_env_file(K8S_ENV_FILE).items() if k in K8S_DEFAULTS})
    # Real environment variables win over the file (handy for CI).
    for key in K8S_DEFAULTS:
        if os.getenv(key):
            settings[key] = os.environ[key]
    return settings


def need_registry(settings: dict):
    if not settings["K8S_REGISTRY"]:
        die("K8S_REGISTRY is not set. Copy k8s.env.example to k8s.env and fill it in.")


def confirm(args, question: str):
    if args.yes:
        return
    answer = input(f"{question} [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("Aborted, nothing changed.")
        sys.exit(0)


def kubectl_context() -> str:
    result = run(["kubectl", "config", "current-context"], capture=True, check=False)
    return result.stdout.strip() or "(none)"


def kubectl_apply_json(manifest: dict):
    run(["kubectl", "apply", "-f", "-"], input_text=json.dumps(manifest))


# --- segment: tag and image names -----------------------------------------

def git_output(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        die(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_tag(args, writing: bool) -> str:
    """Image tag = short commit hash. A dirty tree gets a timestamped tag,
    saved by `build` so push/apply/seed use the same one."""
    if args.tag:
        return args.tag
    require_tools("git")
    sha = git_output("rev-parse", "--short=12", "HEAD")
    dirty = bool(git_output("status", "--porcelain"))
    if not dirty:
        return sha
    if not args.allow_dirty:
        die("working tree has uncommitted changes. Commit them (the image tag is the "
            "commit hash), or pass --allow-dirty for a throwaway dev build.")
    if writing:
        tag = f"{sha}-dirty-{time.strftime('%Y%m%d%H%M%S')}"
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        TAG_FILE.write_text(tag + "\n")
        return tag
    if not TAG_FILE.exists():
        die("dirty tree and no saved tag. Run `build --allow-dirty` first, or pass --tag.")
    return TAG_FILE.read_text().strip()


def image_refs(settings: dict, tag: str) -> tuple:
    need_registry(settings)
    base = f"{settings['K8S_REGISTRY'].rstrip('/')}/{settings['K8S_IMAGE_BASENAME']}"
    return f"{base}-app:{tag}", f"{base}-web:{tag}"


# --- segment: build + push ---------------------------------------------------

def preflight_build():
    require_tools("docker")
    for required in ("requirements.txt", "permissions.conf",
                     "core/k8s/Dockerfile.app", "core/k8s/Dockerfile.web"):
        if not (PROJECT_ROOT / required).exists():
            die(f"missing {required}")

    modules_dir = PROJECT_ROOT / "modules"
    missing = [
        p.name for p in sorted(modules_dir.iterdir())
        if p.is_dir() and not p.name.startswith("_")
        and (p / "schema.sql").exists() and not (p / "table_core.py").exists()
    ]
    if missing:
        die("table_core.py is baked into the image, so it must exist and be committed. "
            f"Missing for: {', '.join(missing)}. Run `python setup.py` (or "
            "core/backend/sync_tables.py --module=<name>) first.")


def cmd_build(args):
    settings = load_settings()
    preflight_build()
    tag = resolve_tag(args, writing=True)
    app_image, web_image = image_refs(settings, tag)
    env = dict(os.environ, DOCKER_BUILDKIT="1")  # per-Dockerfile .dockerignore needs BuildKit

    for dockerfile, image in (("Dockerfile.app", app_image), ("Dockerfile.web", web_image)):
        run([
            "docker", "build",
            "-f", str(K8S_DIR / dockerfile),
            "-t", image,
            "--build-arg", f"SOURCE_URL={settings['SOURCE_URL']}",
            str(PROJECT_ROOT),
        ], env=env)
    print(f"Built:\n  {app_image}\n  {web_image}")


def cmd_push(args):
    settings = load_settings()
    require_tools("docker")
    tag = resolve_tag(args, writing=False)
    app_image, web_image = image_refs(settings, tag)

    if settings["GHCR_USER"] and settings["GHCR_PUSH_TOKEN"]:
        registry_host = settings["K8S_REGISTRY"].split("/")[0]
        run(["docker", "login", registry_host, "-u", settings["GHCR_USER"], "--password-stdin"],
            input_text=settings["GHCR_PUSH_TOKEN"])
    else:
        print("GHCR_USER/GHCR_PUSH_TOKEN not set: assuming `docker login` was already done.")

    for image in (app_image, web_image):
        run(["docker", "push", image])
    print(f"Pushed:\n  {app_image}\n  {web_image}")


# --- segment: .env provisioning ---------------------------------------------------

def ensure_auth_env() -> bool:
    """Makes sure .env carries every auth setting: generates SECRET_KEY when
    blank or shorter than 32 characters, appends missing variables with their
    defaults. Existing values are never overwritten (SECRET_KEY aside).
    Returns True if the file changed."""
    if not ENV_FILE.exists():
        die(".env not found. Copy .env.example to .env and fill in the DB credentials.")

    lines = ENV_FILE.read_text().splitlines()
    line_index = {}
    for i, line in enumerate(lines):
        match = ENV_LINE.match(line)
        if match:
            line_index[match.group(1)] = i

    changed = False

    secret_idx = line_index.get("SECRET_KEY")
    current = lines[secret_idx].split("=", 1)[1].strip() if secret_idx is not None else ""
    if len(current) < MIN_SECRET_LENGTH:
        new_line = f"SECRET_KEY={secrets.token_hex(32)}"
        if secret_idx is None:
            lines.append(new_line)
        else:
            lines[secret_idx] = new_line
        changed = True
        print(f"Generated SECRET_KEY in .env (was missing or shorter than {MIN_SECRET_LENGTH} characters).")

    missing = [(k, v) for k, v in AUTH_ENV_DEFAULTS.items() if k not in line_index]
    if missing:
        lines.append("")
        lines.append("# --- Authentication (added by deploy_k8s.py) ---")
        for key, value in missing:
            lines.append(f"{key}={value}")
        changed = True
        print(f"Added missing auth settings to .env: {', '.join(k for k, _ in missing)}")

    if changed:
        ENV_FILE.write_text("\n".join(lines) + "\n")
        try:
            os.chmod(ENV_FILE, 0o600)
        except OSError:
            pass
    return changed


# --- segment: namespace, config, secrets ---------------------------------------

def namespace_manifest(namespace: str) -> dict:
    # "restricted" is the strictest Pod Security level; the pod spec is
    # written to satisfy it (non-root, no escalation, caps dropped, seccomp).
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": {
                "pod-security.kubernetes.io/enforce": "restricted",
                "pod-security.kubernetes.io/audit": "restricted",
                "pod-security.kubernetes.io/warn": "restricted",
            },
        },
    }


def cmd_config(args):
    settings = load_settings()
    require_tools("kubectl")
    namespace = settings["K8S_NAMESPACE"]

    if (PROJECT_ROOT / "modules" / AUTH_MODULE).is_dir():
        ensure_auth_env()

    env = read_env_file(ENV_FILE)
    if not env:
        die(".env not found or empty. Copy .env.example to .env and fill in the DB credentials.")
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        die(f".env is missing: {', '.join(missing)}")

    config_data = {k: v for k, v in env.items() if k not in SECRET_KEYS}
    secret_data = {k: v for k, v in env.items() if k in SECRET_KEYS and v != ""}

    print(f"kubectl context: {kubectl_context()}")
    kubectl_apply_json(namespace_manifest(namespace))

    kubectl_apply_json({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": CONFIG_MAP, "namespace": namespace},
        "data": config_data,
    })
    kubectl_apply_json({
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": SECRET, "namespace": namespace},
        "stringData": secret_data,
    })

    if settings["GHCR_USER"] and settings["GHCR_PULL_TOKEN"]:
        host = settings["K8S_REGISTRY"].split("/")[0] if settings["K8S_REGISTRY"] else "ghcr.io"
        auth = base64.b64encode(
            f"{settings['GHCR_USER']}:{settings['GHCR_PULL_TOKEN']}".encode()
        ).decode()
        dockerconfig = {"auths": {host: {
            "username": settings["GHCR_USER"],
            "password": settings["GHCR_PULL_TOKEN"],
            "auth": auth,
        }}}
        kubectl_apply_json({
            "apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/dockerconfigjson",
            "metadata": {"name": PULL_SECRET, "namespace": namespace},
            "stringData": {".dockerconfigjson": json.dumps(dockerconfig)},
        })
    else:
        print(f"GHCR_USER/GHCR_PULL_TOKEN not set: no '{PULL_SECRET}' secret created. "
              "Pulls work only if the images are public.")

    print(f"Config applied in namespace '{namespace}': "
          f"{len(config_data)} ConfigMap keys, {len(secret_data)} Secret keys.")


# --- segment: overlays, apply, seed ----------------------------------------------

def write_overlay(name: str, base_dir: Path, settings: dict, tag: str, node: bool) -> Path:
    """Generates .k8s-build/<name>/kustomization.yaml (JSON is valid YAML, so
    no YAML library is needed) pointing at the base, with the real image
    names/tags and the preferred node patched in."""
    app_image, web_image = image_refs(settings, tag)
    registry_base = app_image.rsplit(":", 1)[0]
    web_base = web_image.rsplit(":", 1)[0]

    overlay_dir = BUILD_DIR / name
    overlay_dir.mkdir(parents=True, exist_ok=True)

    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": settings["K8S_NAMESPACE"],
        "resources": [os.path.relpath(base_dir, overlay_dir)],
        "images": [
            {"name": APP_IMAGE_PLACEHOLDER, "newName": registry_base, "newTag": tag},
            {"name": WEB_IMAGE_PLACEHOLDER, "newName": web_base, "newTag": tag},
        ],
    }
    if node and settings["K8S_PREFERRED_NODE"]:
        kustomization["patches"] = [{
            "target": {"kind": "Deployment", "name": DEPLOYMENT},
            "patch": json.dumps([{
                "op": "replace",
                "path": PREFERRED_NODE_PATH,
                "value": settings["K8S_PREFERRED_NODE"],
            }]),
        }]

    (overlay_dir / "kustomization.yaml").write_text(json.dumps(kustomization, indent=2) + "\n")
    return overlay_dir


def require_config(namespace: str):
    for kind, name in (("configmap", CONFIG_MAP), ("secret", SECRET)):
        result = run(["kubectl", "-n", namespace, "get", kind, name],
                     check=False, capture=True)
        if result.returncode != 0:
            die(f"{kind}/{name} not found in namespace '{namespace}'. Run `python deploy_k8s.py config` first.")


def cmd_apply(args):
    settings = load_settings()
    require_tools("kubectl")
    namespace = settings["K8S_NAMESPACE"]
    tag = resolve_tag(args, writing=False)
    app_image, web_image = image_refs(settings, tag)

    print(f"kubectl context : {kubectl_context()}")
    print(f"namespace       : {namespace}")
    print(f"app image       : {app_image}")
    print(f"web image       : {web_image}")
    print(f"preferred node  : {settings['K8S_PREFERRED_NODE'] or '(none)'}")
    confirm(args, "Apply to this cluster?")

    require_config(namespace)
    overlay = write_overlay("app", K8S_DIR / "base", settings, tag, node=True)
    run(["kubectl", "apply", "-k", str(overlay)])
    run(["kubectl", "-n", namespace, "rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=180s"])
    run(["kubectl", "-n", namespace, "get", "pods", "-o", "wide"])


def cmd_seed(args):
    settings = load_settings()
    require_tools("kubectl")
    namespace = settings["K8S_NAMESPACE"]
    tag = resolve_tag(args, writing=False)

    print(f"kubectl context: {kubectl_context()}")
    confirm(args, f"Run the admin seed Job in namespace '{namespace}'?")

    require_config(namespace)
    overlay = write_overlay("seed", K8S_DIR / "jobs", settings, tag, node=False)
    # Job specs are immutable, so replace any previous run.
    run(["kubectl", "-n", namespace, "delete", "job", SEED_JOB, "--ignore-not-found", "--wait=true"])
    run(["kubectl", "apply", "-k", str(overlay)])

    result = run(["kubectl", "-n", namespace, "wait", "--for=condition=complete",
                  f"job/{SEED_JOB}", "--timeout=120s"], check=False)
    run(["kubectl", "-n", namespace, "logs", f"job/{SEED_JOB}"], check=False)
    if result.returncode != 0:
        run(["kubectl", "-n", namespace, "describe", f"job/{SEED_JOB}"], check=False)
        die("seed Job did not complete (see output above).")


# --- segment: status, teardown -----------------------------------------------------

def node_ips() -> list:
    result = run([
        "kubectl", "get", "nodes", "-o",
        'jsonpath={range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\\n"}{end}',
    ], capture=True, check=False)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def cmd_status(args):
    settings = load_settings()
    require_tools("kubectl")
    namespace = settings["K8S_NAMESPACE"]

    run(["kubectl", "-n", namespace, "get", "deploy,pods,svc", "-o", "wide"], check=False)

    print(f"\nHealth check on every node's NodePort {NODE_PORT} (from this machine):")
    failures = 0
    for ip in node_ips():
        url = f"http://{ip}:{NODE_PORT}/api/_health"
        try:
            with urllib.request.urlopen(url, timeout=5) as res:
                print(f"  {ip}: HTTP {res.status} {res.read().decode().strip()}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            failures += 1
            print(f"  {ip}: FAILED ({e})")
    if failures:
        print("WARNING: at least one node did not answer. Check `kubectl -n "
              f"{namespace} describe pod` and kube-proxy.", file=sys.stderr)


def cmd_teardown(args):
    settings = load_settings()
    require_tools("kubectl")
    namespace = settings["K8S_NAMESPACE"]
    print(f"kubectl context: {kubectl_context()}")
    confirm(args, f"DELETE namespace '{namespace}' and everything in it (config, secrets, pods)?")
    run(["kubectl", "delete", "namespace", namespace, "--ignore-not-found"])


def cmd_all(args):
    cmd_build(args)
    cmd_push(args)
    cmd_config(args)
    cmd_apply(args)
    cmd_seed(args)
    cmd_status(args)


# --- segment: main -------------------------------------------------------------------

COMMANDS = {
    "build": cmd_build,
    "push": cmd_push,
    "config": cmd_config,
    "apply": cmd_apply,
    "seed": cmd_seed,
    "status": cmd_status,
    "teardown": cmd_teardown,
    "all": cmd_all,
}


def main():
    parser = argparse.ArgumentParser(description="Build and deploy this app to Kubernetes.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--tag", help="image tag (default: short git commit hash)")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="allow building from a tree with uncommitted changes (dev only)")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompts")
    args = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
