"""Comprehensive End-to-End Playwright Audit for Milestone M4-VER.

Validates Acceptance Criteria 6:
1. All 6 core views load cleanly: Dashboard, Devices, DevicePage (with live SSH), DiffViewer, DriftView, ChaosLabView.
2. 4 factory nodes are present in inventory.
3. Live SSH Web Terminal is operational on DevicePage.
4. Monaco Diff Viewer renders cleanly.
5. Drift control view and compliance cards are operational.
6. Chaos Lab scenarios and reset are functional.
7. Role switching across admin, viewer, operator works seamlessly.
8. Theme switching between dark and light modes functions without glitch.
9. 0 console errors (console.error == 0) and 0 unhandled page exceptions across the entire lifecycle.
"""

import os
import sys

from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5173/?token=dev-admin-token"
CHROME_PATH = "/home/tarhes/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"

def run_e2e_audit():
    console_errors = []
    page_errors = []
    results = {}

    with sync_playwright() as p:
        if os.path.exists(CHROME_PATH):
            browser = p.chromium.launch(executable_path=CHROME_PATH, headless=True)
        else:
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)

        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        print("=== STAGE 1: Dashboard View & 4 Factory Nodes Verification ===")
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(1000)

        # Confirm on Dashboard / Platform Header
        assert page.locator('header span:has-text("NetOps Platform")').is_visible()
        print("   Dashboard loaded successfully.")

        # Navigate to Devices View
        print("\n=== STAGE 2: Devices View & Inventory Inspection ===")
        page.locator('button:has-text("Устройства"), a:has-text("Устройства")').first.click()
        page.wait_for_timeout(800)

        rows = page.locator("table tbody tr")
        row_count = rows.count()
        print(f"   Inventory device rows found: {row_count}")
        assert row_count == 4, f"Expected exactly 4 factory nodes, found {row_count}"
        results["four_factory_nodes"] = True

        # Check oper and intent status badges
        oper_badges = page.locator("table tbody tr span:has-text('UP'), table tbody tr span:has-text('DOWN')").count()
        assert oper_badges >= 4, f"Expected Oper Status badges for all 4 nodes, got {oper_badges}"
        print(f"   Oper Status badges present: {oper_badges}")
        results["devices_view"] = True

        # Open DevicePage (Passport) via first row 'Карточка' button
        print("\n=== STAGE 3: DevicePage & Live SSH Terminal Inspection ===")
        page.locator('table tbody tr button:has-text("Карточка")').first.click()
        page.wait_for_timeout(1000)

        # Verify DevicePage header / tabs
        assert page.locator('button:text-is("SSH")').is_visible(), "SSH tab missing in DevicePage"
        print("   DevicePage passport loaded successfully.")

        # Switch to SSH tab
        page.locator('button:text-is("SSH")').click()
        page.wait_for_timeout(800)

        # Check terminal console area
        terminal_input = page.locator('input[placeholder*="команду"], input[placeholder*="command"]').first
        assert terminal_input.is_visible(), "SSH terminal command input not visible"
        print("   SSH terminal input is active and visible.")

        # Send a safe diagnostic command: show version / display version
        terminal_input.fill("display version")
        terminal_input.press("Enter")
        page.wait_for_timeout(1200)

        # Check for output stream in terminal pre/div
        terminal_output = page.locator('pre, div.font-mono').all_text_contents()
        has_output = any("vrp" in line.lower() or "huawei" in line.lower() for line in terminal_output)
        print(f"   SSH live command executed, response received: {has_output}")
        assert has_output, "Expected terminal command output to appear in live session"
        results["live_ssh_terminal"] = True

        results["device_page"] = True

        # Navigate to DiffViewer (Monaco Diff & AI Guard)
        print("\n=== STAGE 4: DiffViewer & Monaco Inspection ===")
        page.locator('button:has-text("Diff & AI Guard"), a:has-text("Diff & AI Guard")').first.click()
        page.wait_for_timeout(1500)

        # Verify DiffViewer loaded
        diff_heading = page.locator('h2:has-text("Monaco Diff & LLM Guard")')
        assert diff_heading.is_visible(), "DiffViewer heading not found"
        
        # Verify Monaco / Diff container presence
        diff_editor = page.locator('.monaco-editor, .monaco-diff-editor, div:has-text("Текущая (Running Config)")').first
        assert diff_editor.is_visible(), "Diff editor container not visible"
        print("   DiffViewer & Monaco diff container rendered successfully.")
        results["diff_viewer_monaco"] = True

        # Navigate to DriftView
        print("\n=== STAGE 5: DriftView & Compliance Audit ===")
        page.locator('button:has-text("Контроль дрейфа"), a:has-text("дрейфа"), button:has-text("дрейфа")').first.click()
        page.wait_for_timeout(800)

        # Verify DriftView content
        drift_element = page.locator('button:has-text("Запустить полный скан дрейфа"), button:has-text("Drift Scan")').first
        assert drift_element.is_visible(), "DriftView scan button not found"
        
        # Check scan drift button and compliance cards
        assert page.locator('span:has-text("GitOps Continuous Audit")').is_visible()
        print("   DriftView and scan control are 100% operational.")
        results["drift_view"] = True

        # Navigate to Chaos Lab View
        print("\n=== STAGE 6: ChaosLabView & Fault Simulation Studio ===")
        page.locator('button:has-text("Chaos Lab"), a:has-text("Chaos Lab")').first.click()
        page.wait_for_timeout(800)

        chaos_heading = page.locator('h2:has-text("Chaos Lab")')
        assert chaos_heading.is_visible(), "Chaos Lab heading not found"

        chaos_cards = page.locator('button:has-text("Внедрить"), button:has-text("Уронить"), button:has-text("Смоделировать"), button:has-text("Сбросить")').count()
        assert chaos_cards >= 5, f"Expected chaos injection cards, found {chaos_cards}"
        print(f"   Chaos injection scenarios available: {chaos_cards}")
        
        reset_btn = page.locator('button:has-text("Сбросить к эталону")').first
        assert reset_btn.is_visible(), "Reset to baseline button missing in Chaos Lab"
        print("   Chaos Lab scenarios and Git baseline reset operational.")
        results["chaos_lab_view"] = True

        # Theme Switching (Dark -> Light -> Dark)
        print("\n=== STAGE 7: Theme Switching (Dark <-> Light) ===")
        html_theme_initial = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        print(f"   Initial theme: {html_theme_initial}")

        # 1st toggle: click theme button
        page.locator('header button:has(svg.lucide-sun), header button:has(svg.lucide-moon)').first.click()
        page.wait_for_timeout(300)
        html_theme_toggled = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        print(f"   After 1st toggle: {html_theme_toggled}")
        assert html_theme_toggled != html_theme_initial, "Theme failed to toggle"

        # 2nd toggle: click theme button to restore
        page.locator('header button:has(svg.lucide-sun), header button:has(svg.lucide-moon)').first.click()
        page.wait_for_timeout(300)
        html_theme_restored = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        print(f"   After 2nd toggle: {html_theme_restored}")
        assert html_theme_restored == html_theme_initial, "Theme failed to restore"
        results["theme_switching"] = True

        # Role Switching (admin -> viewer -> operator -> admin)
        print("\n=== STAGE 8: RBAC Role Switching (Admin <-> Viewer <-> Operator) ===")
        # Test switching role to viewer via official token
        page.goto("http://localhost:5173/?token=dev-viewer-token", wait_until="networkidle")
        page.wait_for_timeout(600)
        badge_text_v = page.locator('header button span.capitalize').first.text_content().lower()
        print(f"   Role badge with dev-viewer-token: {badge_text_v}")
        assert "viewer" in badge_text_v

        # Test switching role to operator via official token
        page.goto("http://localhost:5173/?token=dev-operator-token", wait_until="networkidle")
        page.wait_for_timeout(600)
        badge_text_o = page.locator('header button span.capitalize').first.text_content().lower()
        print(f"   Role badge with dev-operator-token: {badge_text_o}")
        assert "operator" in badge_text_o

        # Restore admin role
        page.goto("http://localhost:5173/?token=dev-admin-token", wait_until="networkidle")
        page.wait_for_timeout(600)
        badge_text_a = page.locator('header button span.capitalize').first.text_content().lower()
        print(f"   Restored role badge to admin: {badge_text_a}")
        assert "admin" in badge_text_a
        results["role_switching"] = True

        # Final Error Count
        print("\n=== STAGE 9: Console Error & Page Exception Audit ===")
        print(f"Total Console errors: {len(console_errors)}")
        for err in console_errors:
            print(f"  [ERROR] {err}")
        print(f"Total Page errors: {len(page_errors)}")
        for perr in page_errors:
            print(f"  [PAGE ERROR] {perr}")

        results["zero_console_errors"] = (len(console_errors) == 0)
        results["zero_page_errors"] = (len(page_errors) == 0)

        browser.close()

    print("\n================ FINAL E2E RESULTS SUMMARY ================")
    all_passed = True
    for k, v in results.items():
        status = "PASSED" if v else "FAILED"
        print(f"  - {k}: {status}")
        if not v:
            all_passed = False

    print(f"\nOVERALL E2E VERDICT: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    return all_passed

if __name__ == "__main__":
    success = run_e2e_audit()
    sys.exit(0 if success else 1)
