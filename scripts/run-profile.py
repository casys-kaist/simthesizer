import subprocess
import sys

import typer
from simthesizer.harness import (
    PROFILE_DATA_ROOT,
    REPO_ROOT,
    SCRIPT_ROOT,
    profile_output_path,
    profile_receipt_path,
)


def main(model: str = typer.Argument()) -> None:
    receipt = profile_receipt_path(model)
    if not receipt.is_file():
        raise SystemExit(f"profile receipt not found: {receipt}")

    PROFILE_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    output_csv = profile_output_path(model)
    typer.echo(f"[profile] {model}")

    command = [
        sys.executable,
        str(SCRIPT_ROOT / "vllm-profile.py"),
        "--receipt",
        str(receipt),
        "--output-csv",
        str(output_csv),
    ]
    result = subprocess.run(command, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)

    typer.echo(str(output_csv))


if __name__ == "__main__":
    typer.run(main)
