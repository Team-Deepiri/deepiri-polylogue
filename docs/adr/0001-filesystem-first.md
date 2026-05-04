# ADR 0001 — Filesystem-first sync

## Status

Accepted

## Context

We want multiple LLM clients across providers to share awareness without a proprietary bus.

## Decision

Use a project-local directory with JSON metadata and a JSONL journal, accessed via a small Python CLI (stdlib only).

## Consequences

- Works anywhere the tree is visible (local disk, synced folder, devcontainer bind mount).
- No authentication story; trust boundary is the host filesystem.
- Easy backup and grep; harder to scale past very high write rates (acceptable for human+agent teams).
