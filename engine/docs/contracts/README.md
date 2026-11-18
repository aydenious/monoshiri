# Contracts

A contract is a frozen file describing something that crosses from one tree to another. Each side builds and tests against the file and its fixture, not against the other side's code. This one was frozen for the 2.0 launch.

| # | Contract | Producer | Consumer | In use since |
|---|---|---|---|---|
| 3 | [Export bundle](03-export-bundle.md) | `surfsense_backend` export | `surfsense_local` import | 2.0.0 |

## Fixtures

- `export-sample/` is an unzipped contract-3 bundle. Tests zip it in a setup step. Check it with `python3 check-export-sample.py`.

The check is dependency-free and runs from this folder.

Two tests read files in this folder by path. `surfsense_backend/tests/unit/services/test_account_export.py` reads the fixture and `surfsense_backend/tests/integration/test_account_export.py` runs `check-export-sample.py` against a real export. Moving this folder means updating both in the same change, plus the skip entry in `scripts/check_docs.py` and the `docs/contracts/**` path filters in `.github/workflows/docker-tests.yml` and `.github/workflows/desktop-tests.yml`.

## Changing a contract

A contract changes by a pull request that the owners of both sides approve. [CODEOWNERS](../../.github/CODEOWNERS) requests their review, but GitHub accepts one approval, so the second is by agreement. If the change is not backward compatible, bump the version field the contract names (`format` for the bundle) and update the fixture in the same PR. Never change a fixture without changing the contract text that describes it.
