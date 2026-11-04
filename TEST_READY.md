# TEST_READY.md — E2E Requirements Test Suite

**Test Suite Path**: `backend/tests/integration/test_e2e_requirements.py`  
**Execution Command**: `backend/.venv/bin/pytest backend/tests/integration/test_e2e_requirements.py -v`  
**Status**: **READY** (14 passed, 0 failed, 0 errors, Exit code: 0)  
**Date**: 2026-10-21  
**Author**: Test Writer (`test_writer_e2e`)

---

## 1. Test Suite Architecture & Coverage Matrix

The E2E test suite implements opaque-box, requirement-driven tests covering all 4 Tiers:

| Tier | Category | Test Case Method | Target Requirement / Scope | Result | Notes |
|:---|:---|:---|:---|:---:|:---|
| **Tier 1** | Feature | `test_static_qr_viewer_multi_device_login` | R1 / F1.2: Concurrent logins from 3+ distinct devices (`X-Client-ID`, `User-Agent`, IP headers) without `DeviceBoundConflictError` | **PASSED** | Validates session persistence & read access |
| **Tier 1** | Feature | `test_static_qr_token_rotate_instant_401_revocation` | R1 / F1.3: Admin token rotation immediately causes active viewer sessions to fail with 401 on next call; old token reuse rejected | **PASSED** | Validates $O(1)$ token hash invalidation |
| **Tier 1** | Feature | `test_static_qr_token_toggle_disable_instant_401_revocation` | R1 / F1.3: Admin toggle disable immediately invalidates active sessions with 401; re-enabling restores access | **PASSED** | Validates `is_active` DB check |
| **Tier 1** | Feature | `test_strict_403_forbidden_for_viewer_on_mutating_endpoints` | R2 / F2.5: Viewer rejected with 403 Forbidden across 22 mutating endpoints (deploy, dry-run, drift, chaos, emergency, topology, stands, users) | **PASSED** | Strict RBAC enforcement |
| **Tier 1** | Feature | `test_stands_sync_all_requires_admin` | R2 / F2.1: `POST /api/v1/stands/sync-all` restricted to Admin | **PASSED** | Verified `Admin` dependency in `stands.py` |
| **Tier 1** | Feature | `test_sensitive_secret_redaction_for_viewer` | R2 / F2.3: Redaction of user tokens and sessions on `/system/integrations`, recovery key on `/system/bootstrap/status`, jump passwords on `/stands` | **PASSED** | Zero secret leakage to Viewer |
| **Tier 1** | Feature | `test_multitenant_job_isolation_for_scoped_viewer` | R2 / F2.2: Scoped viewer (`allowed_system="fabric:stand-alpha"`) denied access to jobs, diffs, and explanations for `stand-beta` | **PASSED** | Verified `_assert_job_allowed` on diff/explain |
| **Tier 2** | Boundary | `test_invalid_malformed_and_expired_tokens` | Boundary: Rejection of empty (`""`), short (`<6`), blank whitespace, non-existent, expired, and rotated tokens | **PASSED** | 422 for malformed, 401/403 for invalid/expired |
| **Tier 2** | Boundary | `test_rapid_concurrent_logins_10_devices` | Boundary / Concurrency: 10 distinct simulated devices logging in with the same static QR token in rapid succession | **PASSED** | 100% login success, 0 conflicts |
| **Tier 2** | Boundary | `test_empty_blank_and_malformed_payloads` | Boundary: Empty JSON, non-string tokens, oversized tokens (>1000 chars), invalid types on toggle | **PASSED** | 422 Unprocessable Entity |
| **Tier 3** | Combination | `test_viewer_navigates_dashboard_while_admin_rotates_token` | Combination: Active viewer browsing endpoints (`/devices`, `/topology/graph`) revoked mid-flight upon Admin token rotation | **PASSED** | Immediate 401 on next API request |
| **Tier 3** | Combination | `test_operator_deploy_viewer_observes_cannot_cancel_or_mutate` | Combination: Operator runs dry-run; Viewer observes status & SSE log stream but cannot cancel, deploy, or inject chaos | **PASSED** | Viewer observes without mutating |
| **Tier 4** | Scenario | `test_full_viewer_session_lifecycle` | Real-World: End-to-end QR URL parse -> auto-login -> browse dashboard, topology, jobs -> mutation blocked (403) -> logout (204) -> session revoked | **PASSED** | Complete user journey verified |
| **Tier 4** | Scenario | `test_stand_offline_zero_fake_metrics` | Real-World / Honesty: Unreachable stand reports honest 0 in-sync devices and non-zero unreachable/unknown devices; zero fake 100% health | **PASSED** | Radical honesty metrics compliance |

---

## 2. Test Execution Commands

### Standard Runner
```bash
backend/.venv/bin/pytest backend/tests/integration/test_e2e_requirements.py -v
```

### Static Viewer Integration Tests
```bash
backend/.venv/bin/pytest backend/tests/integration/test_static_viewer.py -v
```

### Repository Invariants & AGENTS.md Audit
```bash
backend/.venv/bin/pytest backend/tests/integration/test_repo_data.py -v
```

### Linting Check
```bash
backend/.venv/bin/ruff check backend/tests/integration/test_e2e_requirements.py
```

---

## 3. Remediated Implementation Defects

1. **Defect F2.1 — Mutating Route Admin Enforcement (FIXED)**:
   - **Location**: `backend/netops/api/routes/stands.py`
   - **Resolution**: `POST /api/v1/stands/sync-all` strictly enforces `admin: Admin` dependency.

2. **Defect F2.2 — Multi-Tenant Job Isolation on Diff/Explain (FIXED)**:
   - **Location**: `backend/netops/api/routes/jobs.py`
   - **Resolution**: `GET /api/v1/jobs/{job_id}/diff` and `POST /api/v1/jobs/{job_id}/explain` enforce `user: Viewer` and invoke `_assert_job_allowed(job, user.allowed_system)`.

---

## 4. Verification Evidence

```text
============================= test session starts ==============================
platform linux -- Python 3.12.15, pytest-9.1.1, pluggy-1.6.0
collected 14 items                                                             

backend/tests/integration/test_e2e_requirements.py::TestTier1FeatureCoverage::test_static_qr_viewer_multi_device_login PASSED [  7%]
backend/tests/integration/test_e2e_requirements.py::TestTier1FeatureCoverage::test_static_qr_token_rotate_instant_401_revocation PASSED [ 14%]
backend/tests/integration/test_e2e_requirements.py::TestTier1FeatureCoverage::test_static_qr_token_toggle_disable_instant_401_revocation PASSED [ 21%]
backend/tests/integration/test_e2e_requirements.py::TestTier1FeatureCoverage::test_strict_403_forbidden_for_viewer_on_mutating_endpoints PASSED [ 28%]
backend/tests/integration/test_e2e_requirements.py::TestTier1FeatureCoverage::test_stands_sync_all_requires_admin PASSED [ 35%]
backend/tests/integration/test_e2e_requirements.py::TestTier1FeatureCoverage::test_sensitive_secret_redaction_for_viewer PASSED [ 42%]
backend/tests/integration/test_e2e_requirements.py::TestTier1FeatureCoverage::test_multitenant_job_isolation_for_scoped_viewer PASSED [ 50%]
backend/tests/integration/test_e2e_requirements.py::TestTier2BoundaryCases::test_invalid_malformed_and_expired_tokens PASSED [ 57%]
backend/tests/integration/test_e2e_requirements.py::TestTier2BoundaryCases::test_rapid_concurrent_logins_10_devices PASSED [ 64%]
backend/tests/integration/test_e2e_requirements.py::TestTier2BoundaryCases::test_empty_blank_and_malformed_payloads PASSED [ 71%]
backend/tests/integration/test_e2e_requirements.py::TestTier3CrossFeatureCombinations::test_viewer_navigates_dashboard_while_admin_rotates_token PASSED [ 78%]
backend/tests/integration/test_e2e_requirements.py::TestTier3CrossFeatureCombinations::test_operator_deploy_viewer_observes_cannot_cancel_or_mutate PASSED [ 85%]
backend/tests/integration/test_e2e_requirements.py::TestTier4RealWorldScenarios::test_full_viewer_session_lifecycle PASSED [ 92%]
backend/tests/integration/test_e2e_requirements.py::TestTier4RealWorldScenarios::test_stand_offline_zero_fake_metrics PASSED [100%]

============================== 14 passed in 3.99s ==============================
```
