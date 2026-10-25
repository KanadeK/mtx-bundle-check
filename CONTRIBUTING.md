# Contributing

Please describe a reproducible data problem before adding a feature. Use synthetic
fixtures; never attach private datasets or credentials. Keep changes focused and
add a regression test that demonstrates the incorrect result.

```sh
python -m venv .venv
# Activate the environment, then:
python -m pip install -e . ruff build
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python -m build
```

CI checks Linux, macOS and Windows on Python 3.11 and 3.13. Workflows only
read, test and build; they never commit source or publish releases.
Contributions are licensed under MIT. Preserve third-party attribution.
