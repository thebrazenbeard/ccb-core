"""Provider protocol and deterministic source-file batching for central projection."""

from __future__ import annotations

import base64
from pathlib import Path
import subprocess
from typing import Callable

from .body_bound_oidc import post_body_bound_json


DEFAULT_MAX_FILES = 500
DEFAULT_MAX_FILE_BYTES = 512 * 1024
DEFAULT_MAX_TOTAL_BYTES = 5 * 1024 * 1024


class CentralProjectionError(RuntimeError):
    pass


def _git(repo_dir: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_dir), *args],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise CentralProjectionError(f"GIT_COMMAND_FAILED:{detail}") from exc


def _git_bytes(repo_dir: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_dir), *args],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip() if exc.stderr else str(exc)
        raise CentralProjectionError(f"GIT_COMMAND_FAILED:{detail}") from exc


def collect_projection_files(
    repo_dir: str | Path,
    commits: tuple[str, ...],
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> tuple[dict[str, object], ...]:
    """Collect exact added message blobs from ordered, already-validated commits."""
    if type(max_file_bytes) is not int or max_file_bytes <= 0:
        raise CentralProjectionError("INVALID_MAX_FILE_BYTES")

    repo = Path(repo_dir)
    seen_paths: set[str] = set()
    output: list[dict[str, object]] = []
    for commit in commits:
        parents = _git(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if len(parents) != 2:
            raise CentralProjectionError("PROJECTION_MERGE_COMMIT_FORBIDDEN")
        parent = parents[1]
        raw = _git_bytes(
            repo,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--diff-filter=A",
            "-r",
            "-z",
            parent,
            commit,
            "--",
            "messages",
        )
        paths = sorted(
            value.decode("utf-8")
            for value in raw.split(b"\0")
            if value.endswith(b".md")
        )
        committed_at = _git(repo, "show", "-s", "--format=%cI", commit)
        for path in paths:
            if path in seen_paths:
                raise CentralProjectionError(f"PROJECTION_PATH_REPEATED:{path}")
            seen_paths.add(path)
            content = _git_bytes(repo, "show", f"{commit}:{path}")
            if len(content) > max_file_bytes:
                raise CentralProjectionError(f"MESSAGE_FILE_TOO_LARGE:{path}")
            blob_sha = _git(repo, "rev-parse", f"{commit}:{path}")
            output.append(
                {
                    "path": path,
                    "source_commit": commit,
                    "source_committed_at": committed_at,
                    "blob_sha": blob_sha,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    # Internal-only sizing evidence. Removed before transport.
                    "content_bytes": len(content),
                }
            )
    return tuple(output)


def chunk_projection_files(
    files: tuple[dict[str, object], ...],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> tuple[tuple[dict[str, object], ...], ...]:
    if type(max_files) is not int or max_files <= 0:
        raise CentralProjectionError("INVALID_MAX_FILES")
    if type(max_total_bytes) is not int or max_total_bytes <= 0:
        raise CentralProjectionError("INVALID_MAX_TOTAL_BYTES")
    if not files:
        return ()

    chunks: list[tuple[dict[str, object], ...]] = []
    current: list[dict[str, object]] = []
    current_bytes = 0
    for item in files:
        size = item.get("content_bytes")
        if type(size) is not int or size < 0:
            raise CentralProjectionError("PROJECTION_FILE_SIZE_INVALID")
        if size > max_total_bytes:
            raise CentralProjectionError(f"MESSAGE_FILE_EXCEEDS_BATCH_LIMIT:{item.get('path')}")
        if current and (len(current) >= max_files or current_bytes + size > max_total_bytes):
            chunks.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(item)
        current_bytes += size
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _transport_file(item: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in item.items() if key != "content_bytes"}


class ProviderProjectionClient:
    """Body-bound client for the service-only Edge projection protocol."""

    def __init__(
        self,
        endpoint: str,
        *,
        token_provider: Callable[[str], str],
        transport: Callable[..., dict[str, object]] = post_body_bound_json,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise CentralProjectionError("PROJECTION_ENDPOINT_REQUIRED")
        self.endpoint = endpoint
        self.token_provider = token_provider
        self.transport = transport

    def _call(self, operation: str, **fields: object) -> dict[str, object]:
        payload = {"protocol_version": 2, "operation": operation, **fields}
        result = self.transport(
            self.endpoint,
            payload,
            token_provider=self.token_provider,
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error") if isinstance(result, dict) else None
            raise CentralProjectionError(f"PROVIDER_OPERATION_FAILED:{operation}:{code or 'UNKNOWN'}")
        return result

    def get_lane(self, identity: str, branch: str) -> dict[str, object] | None:
        result = self._call("cursor", identity=identity, branch=branch)
        lane = result.get("lane")
        if lane is None:
            return None
        if not isinstance(lane, dict):
            raise CentralProjectionError("PROVIDER_CURSOR_INVALID")
        return lane

    def set_control_cut(
        self,
        identity: str,
        branch: str,
        control_sha: str,
        topology_sha: str,
    ) -> None:
        self._call(
            "set_control_cut",
            identity=identity,
            branch=branch,
            control_sha=control_sha,
            topology_sha=topology_sha,
        )

    def claim_batch(
        self,
        *,
        identity: str,
        branch: str,
        expected_projected_head: str | None,
        target_head: str,
        control_sha: str,
        topology_sha: str,
        files: tuple[str, ...],
        owner: str,
        lease_seconds: int = 300,
    ) -> dict[str, object]:
        result = self._call(
            "claim_batch",
            identity=identity,
            branch=branch,
            expected_projected_head=expected_projected_head,
            target_head=target_head,
            control_sha=control_sha,
            topology_sha=topology_sha,
            files=list(files),
            owner=owner,
            lease_seconds=lease_seconds,
        )
        batch = result.get("batch")
        if not isinstance(batch, dict):
            raise CentralProjectionError("PROVIDER_CLAIM_INVALID")
        if not isinstance(batch.get("batch_id"), str) or not isinstance(batch.get("claim_token"), str):
            raise CentralProjectionError("PROVIDER_CLAIM_INVALID")
        return batch

    def project_batch(
        self,
        *,
        identity: str,
        branch: str,
        branch_head: str,
        batch_id: str,
        claim_token: str,
        files: tuple[dict[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        result = self._call(
            "project_batch",
            repository="example/ccb-core",
            identity=identity,
            branch=branch,
            ref=f"refs/heads/{branch}",
            branch_head=branch_head,
            lane_identity=identity,
            journal={"batch_id": batch_id, "claim_token": claim_token},
            files=[_transport_file(item) for item in files],
        )
        rows = result.get("results")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise CentralProjectionError("PROVIDER_PROJECT_RESULT_INVALID")
        return tuple(rows)

    def finalize_batch(self, batch_id: str, claim_token: str) -> dict[str, object]:
        result = self._call(
            "finalize_batch",
            batch_id=batch_id,
            claim_token=claim_token,
        )
        lane = result.get("lane")
        if not isinstance(lane, dict):
            raise CentralProjectionError("PROVIDER_FINALIZE_INVALID")
        return lane


__all__ = [
    "CentralProjectionError",
    "ProviderProjectionClient",
    "chunk_projection_files",
    "collect_projection_files",
]
