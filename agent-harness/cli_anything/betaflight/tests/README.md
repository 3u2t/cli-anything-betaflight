# Tests

Run the whole suite:

```bash
pytest cli_anything/betaflight/tests -q
```

The unit tests use synthetic data. They do not need a flight controller or a
compiler.

Hardware tests are skipped unless `BETAFLIGHT_E2E_PORT` is set:

```bash
set BETAFLIGHT_E2E_PORT=COM4
pytest cli_anything/betaflight/tests/test_full_e2e.py -q
```

`test_full_e2e.py` is read-only.
