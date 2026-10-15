# Kubernetes deployment

Second, separate deployment option next to Docker Compose (`core/docker/`,
`setup.py`). Nothing here changes the Compose flow.

## Layout

```
core/k8s/
  Dockerfile.app            gunicorn image, code baked in, non-root
  Dockerfile.web            nginx-unprivileged + frontend assets only
  Dockerfile.*.dockerignore allow-lists: .env and .git never enter a build
  nginx/default.conf        server block, proxies /api/ to 127.0.0.1:8000
  base/                     Deployment + Service (kustomize)
  jobs/                     seed-admin Job (kustomize)
deploy_k8s.py               build, push, config, apply, seed, status, teardown
k8s.env.example             registry, namespace, preferred node, GHCR credentials
.k8s-build/                 generated overlays and saved tag (gitignored)
```

## Architecture

```
client -> <any node IP>:30080 (NodePort, kube-proxy)
       -> pod app-framework (replicas 1, preferred node: jenkins)
            web container  nginx :8080  -> static files
                                        -> /api/ -> 127.0.0.1:8000
            app container  gunicorn :8000 -> 192.168.20.190:5000 (HAProxy VIP)
```

- One pod, two containers sharing localhost. No inter-pod networking.
- The app is stateless (signed-cookie sessions, lockout state in the
  database), so no volumes are used.
- The database stays outside the cluster, reached by IP through the
  Keepalived VIP, so the app does not depend on cluster DNS.

## Scheduling

- `replicas: 1`, `RollingUpdate` with `maxSurge: 1`, `maxUnavailable: 0`: a
  new pod must be Ready before the old one is removed.
- `preferredDuringScheduling` node affinity, weight 100, for the node in
  `K8S_PREFERRED_NODE` (default `jenkins`). It is a preference, so the pod
  can still start on the other node when jenkins is down.
- The preference applies only when the pod is scheduled. After a failover
  the pod stays where it landed. To move it back:
  `kubectl -n app-framework rollout restart deploy/app-framework`.
- NotReady/Unreachable tolerations of 30 seconds (default 300) shorten
  failover after a node loss to about a minute. Rescheduling depends on the
  control plane, which runs only on srv-deploy-eng.

## Prerequisites

- Cluster with a working CNI, kube-proxy and CoreDNS (restored and verified
  in the infrastructure repo's Kubernetes baseline notes).
- `kubectl` pointing at the cluster, `docker` with BuildKit, `git`.
- `modules/<name>/table_core.py` committed for every module: the generated
  table objects are baked into the image, nothing is reflected at runtime.
- `.env` filled in (same file Compose uses) and `k8s.env` copied from
  `k8s.env.example`.
- Both nodes must be able to pull from the registry. Images built with
  `docker` are not visible to containerd, so a registry is required.

## Usage

```
python deploy_k8s.py all                 # build, push, config, apply, seed, status
python deploy_k8s.py build
python deploy_k8s.py push
python deploy_k8s.py config              # namespace, ConfigMap, Secret, pull secret
python deploy_k8s.py apply               # shows context and images, asks to confirm
python deploy_k8s.py seed                # admin seed Job
python deploy_k8s.py status              # pods, service, health on every node
python deploy_k8s.py teardown            # deletes the namespace (asks to confirm)
python deploy_k8s.py apply --tag <tag>   # redeploy or roll back to a given tag
```

- Images are tagged with the short git commit hash, never `latest`. A dirty
  tree is refused unless `--allow-dirty`.
- `DB_PASSWORD`, `SECRET_KEY` and `ADMIN_PASSWORD` go into a Secret, all other
  `.env` keys into a ConfigMap. Secrets are sent to `kubectl` on stdin.
- `SECRET_KEY` is generated into `.env` if blank, same as `setup.py`.
- Use a read-only token (`read:packages`) for `GHCR_PULL_TOKEN`; the push
  token is only used by `docker login` on the build host.

## Security posture

- Namespace enforces Pod Security `restricted`.
- Non-root numeric UIDs, `readOnlyRootFilesystem`, all capabilities dropped,
  `RuntimeDefault` seccomp, no service account token mounted.
- Backend source (`routes.py`, `table_core.py`, `schema.sql`) is not in the
  web image, unlike the Compose nginx, which mounts all of `modules/`.
- Limitations: the cluster uses flannel, which does not enforce
  NetworkPolicy. Kubernetes Secrets are base64 in etcd, and kubeadm does not
  enable encryption at rest by default. The pod and Service are plain HTTP;
  keep `SESSION_COOKIE_SECURE=false` until TLS is terminated in front.

## Memory sizing

Measured on the Compose deployment: app about 93MiB, nginx about 5MiB.
Requests: 128Mi and 16Mi. Limits: 256Mi and 64Mi.
