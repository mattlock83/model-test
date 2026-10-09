"""Build the official GraphWalker Rust CLI at the repository's pinned revision."""

import json
import os
import shutil
import subprocess

from .resources import asset, tools_directory


def setup():
    lock = json.loads(asset("graphwalker.lock.json").read_text())
    home = tools_directory()
    source = home / "graphwalker-rs"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        subprocess.run(["git", "clone", lock["repository"], str(source)], check=True)
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
        raise ValueError("Preserve local GraphWalker source edits before running setup")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if revision != lock["revision"]:
        subprocess.run(["git", "fetch", "--depth", "1", "origin", lock["revision"]], cwd=source, check=True)
        subprocess.run(["git", "checkout", "--detach", lock["revision"]], cwd=source, check=True)
    env = os.environ.copy()
    local_cargo = home / "cargo/bin/cargo"
    if local_cargo.exists():
        cargo = str(local_cargo)
        env.update(
            CARGO_HOME=str(home / "cargo"),
            RUSTUP_HOME=str(home / "rustup"),
            PATH=str(local_cargo.parent) + os.pathsep + env.get("PATH", ""),
        )
    else:
        cargo = shutil.which("cargo")
    if not cargo:
        raise ValueError("Install Rust 1.88+ from https://rustup.rs, then rerun model-test setup")
    subprocess.run(
        [
            cargo,
            "build",
            "--release",
            "--locked",
            "--bin",
            "graphwalker",
            "--target-dir",
            str(source / "target"),
        ],
        cwd=source,
        env=env,
        check=True,
    )
    print("GraphWalker ready:", source / "target/release/graphwalker")
