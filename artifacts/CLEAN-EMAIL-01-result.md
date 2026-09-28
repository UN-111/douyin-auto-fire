# CLEAN-EMAIL-01 result

- **Task type:** IMPLEMENT
- **Result:** PASS locally; legacy runtime/deployment/config/test files were pruned and the current scheduled workflow now sends a QQ SMTP alert only after a failed `send` job.
- **Commit:** recorded locally after validation; no push or merge performed.

## Runtime path checked

The live path is `main.py` → `core.tasks` → `core.browser` / `core.douyin_im` with configuration from `utils.config` and GitHub environment export from `utils.export_github_env`. The workflow path is `.github/workflows/schedule.yml` → `validate-config` or gated `send` → optional `email-on-failure`. The retained focused test is `tests/test_core_sticker_safety.py`.

The removed files were the unreachable `app/` runtime, `run.py`, old shell/login scripts, systemd units, old task/account/sticker examples, `core/msg_builder.py`, `utils/hitokoto.py`, and tests that imported the removed `app` runtime. Empty legacy directories were removed from the checkout as well.

## Workflow and email behavior

- Schedule remains `0 18 * * *` UTC (Beijing 02:00); `ENABLE_DOUYIN_SPARK_FLOW` must equal `true` for a real send.
- `config/github-actions.tasks.json` remains account/unique ID `601501187I3`, fixed fingerprint `douyin-601501187I3`, and five targets. The workflow still pins native `续火花`, 3–8 second inter-recipient delay, dynamic `COOKIES_<unique_id>`, and optional `PROXY_ADDRESS`.
- `email-on-failure` has `needs: send` and `if: ${{ always() && needs.send.result == 'failure' }}`. It therefore does not run for successful sends, validation-only runs, or skipped sends.
- The job uses only Python standard-library SMTP over QQ SSL (`smtp.qq.com:465`) and existing names `QQ_SMTP_USERNAME`, `QQ_SMTP_AUTH_CODE`, `ALERT_EMAIL_TO`, and optional `ALERT_EMAIL_CC`. No secret values were listed, changed, or emitted.

## Documentation and local evidence

`README.md` now documents the retained runtime, task contract, existing secret names, gate, and failure-email behavior. `.env.example` contains only the current local task shape and non-secret defaults.

Checks passed:

```text
python3 -m py_compile main.py core/*.py utils/*.py
python3 -m pytest -q                 # 3 passed
current runtime import smoke check    # passed with local cloakbrowser stub
workflow YAML/static contract checks  # passed; embedded email Python compiled
legacy path and stale README checks   # passed
git diff --check                       # passed
```

No GitHub API or secret-management operation was used. The only local environment change for validation was installing the already-declared `pytest` and `pytest-asyncio` test runners.
