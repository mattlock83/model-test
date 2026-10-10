"""Build and bundle the native worker in local platform wheels."""

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from packaging.tags import sys_tags


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        environment = os.environ.copy()
        cargo = shutil.which("cargo")
        local = root / ".tools" / "cargo"
        if not cargo and (local / "bin" / "cargo").is_file():
            cargo = str(local / "bin" / "cargo")
            environment.update(CARGO_HOME=str(local), RUSTUP_HOME=str(root / ".tools" / "rustup"))
            environment["PATH"] = str(local / "bin") + os.pathsep + environment["PATH"]
        if not cargo:
            raise RuntimeError("Building Testwalker from source requires Rust 1.88+ and Cargo")
        subprocess.run(
            [
                cargo,
                "build",
                "--release",
                "--locked",
                "--package",
                "testwalker-core",
                "--target-dir",
                str(root / "target"),
            ],
            cwd=root,
            env=environment,
            check=True,
        )
        name = "testwalker-core.exe" if os.name == "nt" else "testwalker-core"
        binary = root / "target" / "release" / name
        build_data["pure_python"] = False
        build_data["tag"] = "py3-none-" + next(sys_tags()).platform
        build_data["force_include"][str(binary)] = "testwalker/bin/" + name
