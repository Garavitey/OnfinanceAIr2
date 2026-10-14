"""Deterministic Mermaid parse gate — Python side.

Shells out to the Node sidecar at validator/validate.mjs, which loads
mermaid.js under jsdom and runs mermaid.parse() against the given source.
The sidecar always exits 0 and reports validity via a JSON line on stdout.
"""

import asyncio
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR_PATH = REPO_ROOT / "validator" / "validate.mjs"


async def validate_mermaid(source: str) -> tuple[bool, str | None]:
    """Validate Mermaid source via the Node sidecar.

    Returns (True, None) if the diagram parses, or (False, error_message)
    if it doesn't.
    """
    proc = await asyncio.create_subprocess_exec(
        "node",
        str(VALIDATOR_PATH),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    stdout, stderr = await proc.communicate(input=source.encode())

    if proc.returncode != 0:
        return False, f"validator process failed: {stderr.decode().strip()}"

    try:
        result = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return False, f"validator produced non-JSON output: {stdout.decode()!r}"

    if result.get("ok"):
        return True, None
    return False, result.get("error", "unknown validation error")


if __name__ == "__main__":
    GOOD_DIAGRAM = """graph TD
  A-->B
  B-->C
"""

    BROKEN_DIAGRAM = """graph TD
  A--->>>B
  this is not valid mermaid at all ][
"""

    async def _main() -> None:
        ok, error = await validate_mermaid(GOOD_DIAGRAM)
        print("good diagram:", {"ok": ok, "error": error})

        ok, error = await validate_mermaid(BROKEN_DIAGRAM)
        print("broken diagram:", {"ok": ok, "error": error})

    asyncio.run(_main())
