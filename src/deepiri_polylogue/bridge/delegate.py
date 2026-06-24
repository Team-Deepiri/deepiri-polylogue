"""Signed cross-agent delegation — user-attested tasks over the live bridge."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import utc_now_iso
from .protocol import encode


@dataclass(frozen=True)
class DelegateRequest:
    delegate_id: str
    room: str
    sender: str
    target: str
    on_behalf_of: str
    sender_provider: str
    prompt: str
    cwd: str
    ts: str
    signature: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "delegate",
            "delegate_id": self.delegate_id,
            "room": self.room,
            "from": self.sender,
            "to": self.target,
            "on_behalf_of": self.on_behalf_of,
            "sender_provider": self.sender_provider,
            "prompt": self.prompt,
            "cwd": self.cwd,
            "ts": self.ts,
            "sig": self.signature,
        }


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "polylogue"


def secret_path() -> Path:
    return config_dir() / "delegate.secret"


def user_path() -> Path:
    return config_dir() / "delegate.user"


def inbox_path(participant_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in participant_id)
    base = Path(os.environ.get("POLYLOGUE_BRIDGE_STATE_DIR", "/tmp"))
    return base / f"polylogue-delegate-inbox-{safe}.jsonl"


def init_delegate_identity(*, user: str | None = None) -> dict[str, str]:
    config_dir().mkdir(parents=True, exist_ok=True)
    if not secret_path().is_file():
        secret_path().write_text(secrets.token_hex(32), encoding="utf-8")
        secret_path().chmod(0o600)
    resolved_user = user or os.environ.get("POLYLOGUE_DELEGATE_USER") or os.environ.get("USER") or "user"
    user_path().write_text(resolved_user.strip() + "\n", encoding="utf-8")
    return {"user": resolved_user, "secret_file": str(secret_path())}


def delegate_user() -> str:
    if user_path().is_file():
        return user_path().read_text(encoding="utf-8").strip()
    return os.environ.get("POLYLOGUE_DELEGATE_USER") or os.environ.get("USER") or "user"


def _load_secret() -> bytes:
    if secret_path().is_file():
        return secret_path().read_text(encoding="utf-8").strip().encode("utf-8")
    env = os.environ.get("POLYLOGUE_DELEGATE_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    raise FileNotFoundError(
        "No delegate signing key. Run: deepiri-polylogue delegate init"
    )


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    body = {
        "delegate_id": payload["delegate_id"],
        "room": payload["room"],
        "from": payload["from"],
        "to": payload["to"],
        "on_behalf_of": payload["on_behalf_of"],
        "sender_provider": payload.get("sender_provider", "unknown"),
        "prompt": payload["prompt"],
        "cwd": payload["cwd"],
        "ts": payload["ts"],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_delegate(payload: dict[str, Any]) -> str:
    digest = hmac.new(_load_secret(), _canonical_bytes(payload), hashlib.sha256).hexdigest()
    return digest


def verify_delegate(payload: dict[str, Any]) -> bool:
    try:
        sig = str(payload.get("sig", ""))
        if not sig:
            return False
        expected = sign_delegate(payload)
        return hmac.compare_digest(sig, expected)
    except (FileNotFoundError, KeyError, TypeError):
        return False


def build_delegate(
    *,
    room: str,
    sender: str,
    target: str,
    prompt: str,
    cwd: str,
    sender_provider: str,
    on_behalf_of: str | None = None,
) -> DelegateRequest:
    if not secret_path().is_file():
        init_delegate_identity()
    delegate_id = str(uuid.uuid4())
    ts = utc_now_iso()
    body = {
        "delegate_id": delegate_id,
        "room": room,
        "from": sender,
        "to": target,
        "on_behalf_of": on_behalf_of or delegate_user(),
        "sender_provider": sender_provider,
        "prompt": prompt,
        "cwd": str(Path(cwd).resolve()),
        "ts": ts,
    }
    signature = sign_delegate(body)
    return DelegateRequest(
        delegate_id=delegate_id,
        room=room,
        sender=sender,
        target=target,
        on_behalf_of=body["on_behalf_of"],
        sender_provider=sender_provider,
        prompt=prompt,
        cwd=body["cwd"],
        ts=ts,
        signature=signature,
    )


def format_injected_prompt(req: DelegateRequest | dict[str, Any]) -> str:
    if isinstance(req, dict):
        req = DelegateRequest(
            delegate_id=str(req["delegate_id"]),
            room=str(req["room"]),
            sender=str(req["from"]),
            target=str(req["to"]),
            on_behalf_of=str(req["on_behalf_of"]),
            sender_provider=str(req.get("sender_provider", "unknown")),
            prompt=str(req["prompt"]),
            cwd=str(req["cwd"]),
            ts=str(req["ts"]),
            signature=str(req.get("sig", "")),
        )
    return (
        f"[Polylogue delegate — on behalf of {req.on_behalf_of} via {req.sender} ({req.sender_provider})]\n\n"
        f"{req.prompt.strip()}\n\n"
        f"---\n"
        f"Repo: {req.cwd}\n"
        f"Delegate id: {req.delegate_id}"
    )


def append_inbox(participant_id: str, payload: dict[str, Any]) -> Path:
    path = inbox_path(participant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(encode(payload) + "\n")
    return path


def encode_delegate_wire(req: DelegateRequest) -> str:
    return encode(req.to_wire())
