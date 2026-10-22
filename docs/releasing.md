# Releasing

The maintainer cuts releases; nothing here is automated, and no release is tagged by the build
tooling. Version `0.1.0` is the first.

## Checklist

Before tagging, all of these hold:

- [ ] `CHANGELOG.md` has a section for the version, with today's date.
- [ ] The version is the same in `pyproject.toml` and `src/gao_idl/_version.py`, and in
      `CITATION.cff` (`tests/test_version.py` checks the first two).
- [ ] The [acceptance test](ACCEPTANCE.md) was done by a second person, and its checklist is filled
      in.
- [ ] CI is green on the commit to be tagged, on both Python versions (3.11 and 3.13), including the
      docs job.
- [ ] The local release gate below passes on that same commit, from a clean checkout.

## Release gate

From a clean checkout of the commit, in a fresh virtualenv:

```bash
pip install ".[dev,llm,docs]"
ruff check . && ruff format --check . && mypy src/gao_idl
pytest -q
rm -rf dist && python -m build
twine check dist/*
bash scripts/clean_install.sh
mkdocs build --strict
```

`scripts/clean_install.sh` installs `dist/gao_idl-<version>-*.whl` for the version in
`pyproject.toml` and ignores other wheels, but it never deletes anything from `dist/`, so clear
`dist/` yourself (as above) when you want to be sure only this release is there.

## Tag and publish

```bash
git tag -a v0.1.0 -m "gao_idl 0.1.0"
git push --tags

python -m build
twine upload dist/*
```

Use `twine upload --repository testpypi dist/*` first if you want a dry run on TestPyPI. A version
uploaded to PyPI cannot be replaced, so a mistake means a new version number.

After the upload, check that `pip install gao-idl==0.1.0` works in a new virtualenv and that
`python -c "import gao_idl; gao_idl.selftest()"` passes. If you register the project with Zenodo,
put the DOI into `CITATION.cff` in the next commit.
