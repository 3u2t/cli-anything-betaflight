# Submitting this harness to CLI-Anything

Checklist derived from
[CONTRIBUTING.md](https://github.com/HKUDS/CLI-Anything/blob/main/CONTRIBUTING.md).
Two routes are possible; the harness is compliant for both except for the
items marked **TODO**.

## Compliance status

| Requirement | Status |
|-------------|--------|
| `<SOFTWARE>.md` SOP at `<software>/agent-harness/` | âœ… `BETAFLIGHT.md` |
| Canonical `skills/cli-anything-betaflight/SKILL.md` | âœ… |
| Packaged `cli_anything/betaflight/skills/SKILL.md` | âœ… |
| Unit tests pass without backend | âœ… `test_core.py` (+ `test_cli.py`, `test_device.py`, `test_build.py`) |
| E2E tests named `test_full_e2e.py` | âœ… (hardware-gated, skipped without a controller) |
| Unmodified `utils/repl_skin.py` | âœ… identical to the plugin copy |
| `--json` on every command | âœ… |
| README links the harness | âœ… in this repo; **TODO** in the monorepo README |
| `registry.json` entry | **TODO** |
| Installable with `pip install` | âœ… local editable; **TODO** publish or use a `git+https` URL |

Current test run: **59 unit tests passed, 4 hardware E2E tests skipped**.

## Option 1 â€” in-repo harness

1. Fork `HKUDS/CLI-Anything`.
2. Copy the package so the directory becomes `betaflight/agent-harness/`
   (this repo is called `betaflight-cli/` locally; only the copy matters):
   * `betaflight/agent-harness/setup.py`
   * `betaflight/agent-harness/BETAFLIGHT.md`
   * `betaflight/agent-harness/cli_anything/**`
3. Copy the skill to `skills/cli-anything-betaflight/SKILL.md` (repo root).
4. Add to `registry.json`:

```json
{
  "name": "betaflight",
  "display_name": "Betaflight",
  "version": "1.0.0",
  "description": "Inspect board configs, build/flash firmware, and configure flight controllers over MSP",
  "requires": "MSYS2 + ARM GNU GCC 13.3.1 for builds, dfu-util for DFU flashing",
  "homepage": "https://betaflight.com",
  "source_url": null,
  "install_cmd": "pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=betaflight/agent-harness",
  "entry_point": "cli-anything-betaflight",
  "skill_md": "skills/cli-anything-betaflight/SKILL.md",
  "category": "automation",
  "contributors": [
    {"name": "3u2t", "url": "https://github.com/3u2t"}
  ]
}
```

5. Update the monorepo README:
   * add a `betaflight/agent-harness/` line to the layout tree,
   * add a Test Summary row: `betaflight 59 passed (55 unit + 4 e2e skipped)`,
   * bump the TOTAL count,
   * add Betaflight to the relevant capability table.
6. Run `python -m pytest betaflight/agent-harness/cli_anything/betaflight/tests -q`
   from the monorepo root, then open the PR.

## Option 2 â€” standalone repository (recommended here)

The harness already lives in its own directory and does not depend on the
monorepo. This route needs no code in CLI-Anything.

1. Push `betaflight-cli/` to GitHub (for example
   `https://github.com/3u2t/cli-anything-betaflight`).
2. Make it installable from git:

   ```bash
   pip install "git+https://github.com/3u2t/cli-anything-betaflight.git#subdirectory=agent-harness"
   ```

   Optional: publish `cli-anything-betaflight` to PyPI from `agent-harness/`.
3. Add a registry-only entry:

```json
{
  "name": "betaflight",
  "display_name": "Betaflight",
  "version": "1.0.0",
  "description": "Inspect board configs, build/flash firmware, and configure flight controllers over MSP",
  "requires": "MSYS2 + ARM GNU GCC 13.3.1 for builds, dfu-util for DFU flashing",
  "homepage": "https://betaflight.com",
  "source_url": "https://github.com/3u2t/cli-anything-betaflight",
  "install_cmd": "pip install git+https://github.com/3u2t/cli-anything-betaflight.git#subdirectory=agent-harness",
  "entry_point": "cli-anything-betaflight",
  "skill_md": "https://github.com/3u2t/cli-anything-betaflight/blob/main/skills/cli-anything-betaflight/SKILL.md",
  "category": "automation",
  "contributors": [
    {"name": "3u2t", "url": "https://github.com/3u2t"}
  ]
}
```

4. PR only `registry.json` to `HKUDS/CLI-Anything`.

## Notes

* `category` is `automation` because the hub has no `embedded`/`hardware`
  category; adjust if a better one is added.
* CI on Linux cannot run the hardware tests; they skip unless
  `BETAFLIGHT_E2E_PORT` is set.
* The build tests do not compile firmware; they only verify toolchain
  discovery, so CI stays fast.

