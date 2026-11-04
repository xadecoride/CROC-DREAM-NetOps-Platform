"""Automated empirical probe for Milestone 3 action ergonomics, keyboard shortcuts, DryRunModal, and Copilot actions.

Validates:
1. Escape priority order across stacked overlays:
   - DryRunModal + CommandPalette (DryRunModal closes 1st, Palette closes 2nd)
   - CopilotPanel + CommandPalette (Palette closes 1st, Copilot closes 2nd)
   - CopilotPanel + DryRunModal + CommandPalette (DryRunModal closes 1st, Palette closes 2nd, Copilot closes 3rd)
2. Ctrl+K opening and toggling CommandPalette (including when input is focused)
3. Enter key in DryRunModal executing simulation and navigating to jobs view
4. Copilot panel screen context passing and 1-click action resolution button execution
5. 0 console errors and 0 page exceptions
"""

import json
import sys

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:5173/?token=dev-admin-token"

def run_probe():
    results = {}
    console_errors = []
    page_errors = []

    with sync_playwright() as p:
        import os
        chrome_path = "/home/tarhes/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
        if os.path.exists(chrome_path):
            browser = p.chromium.launch(executable_path=chrome_path, headless=True)
        else:
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        print("=== STAGE 1: Initial Page Load ===")
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(1000)

        # -------------------------------------------------------------
        # TEST SUITE 1: ESCAPE PRIORITY ORDER ACROSS STACKED OVERLAYS
        # -------------------------------------------------------------
        print("\n=== STAGE 2: Escape Priority Across Stacked Overlays ===")

        # Scenario 1.1: Double-stack: DryRunModal + CommandPalette
        print("--- Scenario 1.1: Double-stack (DryRunModal + CommandPalette) ---")
        # Ensure we are on Devices tab
        page.locator('button:has-text("Устройства"), a:has-text("Устройства")').first.click()
        page.wait_for_timeout(500)

        # Open DryRunModal via row button
        page.locator('table tbody tr button:has-text("Dry-run")').first.click()
        page.wait_for_timeout(400)
        assert page.locator('h2:has-text("Dry-Run")').is_visible(), "DryRunModal failed to open"

        # Open CommandPalette via Ctrl+K on top of DryRunModal
        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)
        palette_open = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        modal_open = page.locator('h2:has-text("Dry-Run")').is_visible()
        print(f"   Stacked state: DryRunModal={modal_open}, Palette={palette_open}")
        assert palette_open and modal_open, "Both DryRunModal and CommandPalette must be open"

        # 1st Esc: Priority 1 is DryRunModal!
        # DryRunModal must close, CommandPalette must remain open
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        modal_after_1st = page.locator('h2:has-text("Dry-Run")').is_visible()
        palette_after_1st = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        print(f"   After 1st Esc: DryRunModal closed={not modal_after_1st}, Palette still open={palette_after_1st}")
        assert (not modal_after_1st) and palette_after_1st, "1st Esc must close DryRunModal while keeping CommandPalette open"

        # 2nd Esc: Priority 2 is CommandPalette!
        # CommandPalette must close
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        palette_after_2nd = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        print(f"   After 2nd Esc: Palette closed={not palette_after_2nd}")
        assert not palette_after_2nd, "2nd Esc must close CommandPalette"
        results["dryrun_palette_priority"] = True

        # Scenario 1.2: Double-stack: CopilotPanel + CommandPalette
        print("\n--- Scenario 1.2: Double-stack (CopilotPanel + CommandPalette) ---")
        # Open Copilot from Header
        page.locator('button[title*="Ассистент"], button[title*="Assistant"], button:has-text("Copilot"), button:has-text("ИИ")').first.click()
        page.wait_for_timeout(400)
        assert page.locator('aside:has-text("AI Copilot")').is_visible(), "Copilot failed to open"

        # Open CommandPalette via Ctrl+K
        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)
        assert page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible(), "Palette failed to open"

        # 1st Esc: CommandPalette has higher priority than CopilotPanel -> CommandPalette closes
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        palette_after_1st = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        copilot_after_1st = page.locator('aside:has-text("AI Copilot")').is_visible()
        print(f"   After 1st Esc: Palette closed={not palette_after_1st}, Copilot still open={copilot_after_1st}")
        assert (not palette_after_1st) and copilot_after_1st, "1st Esc must close Palette while keeping Copilot open"

        # 2nd Esc: CopilotPanel closes
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        copilot_after_2nd = page.locator('aside:has-text("AI Copilot")').is_visible()
        print(f"   After 2nd Esc: Copilot closed={not copilot_after_2nd}")
        assert not copilot_after_2nd, "2nd Esc must close CopilotPanel"
        results["copilot_palette_priority"] = True

        # Scenario 1.3: Triple-stack: CopilotPanel + DryRunModal + CommandPalette
        print("\n--- Scenario 1.3: Triple-stack (CopilotPanel + DryRunModal + CommandPalette) ---")
        # 1. Open Copilot
        page.locator('button[title*="Ассистент"], button[title*="Assistant"], button:has-text("Copilot"), button:has-text("ИИ")').first.click()
        page.wait_for_timeout(400)
        assert page.locator('aside:has-text("AI Copilot")').is_visible()

        # 2. Ask Copilot prompt to trigger a dry_run action button
        copilot_input = page.locator('aside input[type="text"], aside input')
        copilot_input.fill("Запусти префлайт dry-run для leaf-1")
        copilot_input.press("Enter")
        
        # Wait for Copilot response to arrive and render action button
        action_btn = page.locator('aside button[title^="Выполнить:"]').first
        action_btn.wait_for(state="visible", timeout=15000)
        assert action_btn.is_visible(), "Action button must be rendered in Copilot"

        # Click the 1-click action button in Copilot to open DryRunModal
        action_btn.click()
        dry_run_title = page.locator('h2:has-text("Dry-Run")')
        dry_run_title.wait_for(state="visible", timeout=5000)
        assert dry_run_title.is_visible(), "DryRunModal failed to open via Copilot action"

        # 3. Press Ctrl+K to open CommandPalette
        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)
        assert page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible(), "CommandPalette failed to open"

        # Now all 3 are open!
        s_copilot = page.locator('aside:has-text("AI Copilot")').is_visible()
        s_dryrun = page.locator('h2:has-text("Dry-Run")').is_visible()
        s_palette = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        print(f"   Triple-Stack state: DryRunModal={s_dryrun}, Palette={s_palette}, Copilot={s_copilot}")
        assert s_copilot and s_dryrun and s_palette, "All three overlays must be simultaneously open"

        # 1st Esc: Priority 1 -> DryRunModal closes!
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        s1_dryrun = page.locator('h2:has-text("Dry-Run")').is_visible()
        s1_palette = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        s1_copilot = page.locator('aside:has-text("AI Copilot")').is_visible()
        print(f"   After 1st Esc: DryRunModal closed={not s1_dryrun}, Palette open={s1_palette}, Copilot open={s1_copilot}")
        assert (not s1_dryrun) and s1_palette and s1_copilot, "1st Esc must close DryRunModal only"

        # 2nd Esc: Priority 2 -> CommandPalette closes!
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        s2_dryrun = page.locator('h2:has-text("Dry-Run")').is_visible()
        s2_palette = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        s2_copilot = page.locator('aside:has-text("AI Copilot")').is_visible()
        print(f"   After 2nd Esc: DryRunModal closed={not s2_dryrun}, Palette closed={not s2_palette}, Copilot open={s2_copilot}")
        assert (not s2_dryrun) and (not s2_palette) and s2_copilot, "2nd Esc must close CommandPalette only"

        # 3rd Esc: Priority 3 -> CopilotPanel closes!
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        s3_dryrun = page.locator('h2:has-text("Dry-Run")').is_visible()
        s3_palette = page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()
        s3_copilot = page.locator('aside:has-text("AI Copilot")').is_visible()
        print(f"   After 3rd Esc: All closed -> DryRun={s3_dryrun}, Palette={s3_palette}, Copilot={s3_copilot}")
        assert (not s3_dryrun) and (not s3_palette) and (not s3_copilot), "3rd Esc must close CopilotPanel"
        results["triple_stack_priority"] = True

        # -------------------------------------------------------------
        # TEST SUITE 2: CTRL+K OPENING AND TOGGLING COMMANDPALETTE
        # -------------------------------------------------------------
        print("\n=== STAGE 3: Ctrl+K Opening and Toggling CommandPalette ===")
        # Check initial state: closed
        assert not page.locator('input[placeholder="Устройство, IP или раздел"]').is_visible()

        # Press Ctrl+K -> should open
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        palette_input = page.locator('input[placeholder="Устройство, IP или раздел"]')
        assert palette_input.is_visible(), "Ctrl+K failed to open CommandPalette"
        print("   Ctrl+K opened CommandPalette: True")

        # Verify search input is focused and can take input
        page.keyboard.type("leaf-1")
        page.wait_for_timeout(200)
        assert palette_input.input_value() == "leaf-1", "Palette input typing failed"
        print(f"   Typed in palette input: {palette_input.input_value()}")

        # Now press Ctrl+K while input is focused -> should TOGGLE closed!
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        palette_closed = not palette_input.is_visible()
        print(f"   Ctrl+K while input focused toggled palette closed: {palette_closed}")
        assert palette_closed, "Ctrl+K must toggle CommandPalette closed even when input is focused"

        # Press Ctrl+K again -> should reopen clean
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        assert palette_input.is_visible(), "Ctrl+K failed to reopen CommandPalette"
        assert palette_input.input_value() == "", "Reopened palette should have cleared input"

        # Dismiss via Esc
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert not palette_input.is_visible()
        results["ctrl_k_toggle"] = True

        # -------------------------------------------------------------
        # TEST SUITE 3: ENTER KEY IN DRYRUNMODAL EXECUTING SIMULATION
        # -------------------------------------------------------------
        print("\n=== STAGE 4: Enter Key in DryRunModal Executing Simulation ===")
        # Ensure we are on Devices tab
        page.locator('button:has-text("Устройства"), a:has-text("Устройства")').first.click()
        page.wait_for_timeout(500)

        # Open DryRunModal via row button
        page.locator('table tbody tr button:has-text("Dry-run")').first.click()
        page.wait_for_timeout(400)
        modal_title = page.locator('h2:has-text("Dry-Run")')
        assert modal_title.is_visible(), "DryRunModal failed to open"

        # Verify content requirements
        has_git_sot = page.locator('text=Источник эталона (Git SoT)').is_visible()
        has_lint = page.locator('text=Pre-flight Lint & Синтаксис').is_visible()
        has_blast = page.locator('text=Оценка зоны поражения').is_visible()
        has_enter_hint = page.locator('kbd:has-text("Enter")').is_visible()
        print(f"   DryRunModal details: GitSoT={has_git_sot}, Lint={has_lint}, BlastRadius={has_blast}, EnterHint={has_enter_hint}")
        assert has_git_sot and has_lint and has_blast and has_enter_hint

        # Press Enter to execute simulation
        print("   Pressing Enter key to trigger simulation...")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)

        # DryRunModal should close
        assert not modal_title.is_visible(), "DryRunModal should close after Enter confirmation"

        # Active tab should have transitioned to JobsView ("История Пайплайнов")
        jobs_heading = page.locator('h3:has-text("История Пайплайнов")')
        assert jobs_heading.is_visible(), "Application should transition to JobsView after Dry-Run execution"
        print("   Successfully transitioned to JobsView via Enter key in DryRunModal!")
        results["dryrun_enter_execution"] = True

        # -------------------------------------------------------------
        # TEST SUITE 4: COPILOT PANEL SCREEN CONTEXT & 1-CLICK ACTION
        # -------------------------------------------------------------
        print("\n=== STAGE 5: Copilot Screen Context Passing & 1-Click Action Resolution ===")

        captured_requests = []
        def handle_route(route, request):
            if "/api/v1/copilot/chat" in request.url:
                try:
                    payload = json.loads(request.post_data)
                    captured_requests.append(payload)
                except (TypeError, ValueError) as exc:
                    print(f"      [WARN] Failed to parse copilot chat post_data ({exc!r}): {request.post_data!r}")
            route.continue_()

        page.route("**/api/v1/copilot/chat", handle_route)

        # Subtest 4.1: Screen context passing across tabs
        screens_to_test = [
            ("dashboard", "Дашборд"),
            ("devices", "Устройства"),
            ("diff", "Diff & AI Guard"),
        ]

        for screen_id, tab_label in screens_to_test:
            print(f"   Testing Copilot screen context for: {screen_id} ('{tab_label}')...")
            # Navigate to tab
            page.locator(f'button:has-text("{tab_label}"), a:has-text("{tab_label}")').first.click()
            page.wait_for_timeout(400)

            # Ensure Copilot is open
            if not page.locator('aside:has-text("AI Copilot")').is_visible():
                page.locator('button[title*="Ассистент"], button[title*="Assistant"], button:has-text("Copilot"), button:has-text("ИИ")').first.click()
                page.wait_for_timeout(400)

            # Check screen badge in Copilot header
            header_badge = page.locator(f'aside header span, aside span:has-text("{screen_id}")').first
            print(f"      Copilot header shows screen: '{header_badge.text_content().strip()}'")

            # Wait for any previous request to finish
            if page.locator('text=Copilot формулирует ответ').is_visible():
                page.locator('text=Copilot формулирует ответ').wait_for(state="hidden", timeout=15000)

            # Send a question
            captured_requests.clear()
            input_box = page.locator('aside input[type="text"], aside input')
            input_box.fill(f"Статус на экране {screen_id}?")
            input_box.press("Enter")
            page.wait_for_timeout(800)

            assert len(captured_requests) > 0, f"Request to /api/v1/copilot/chat not captured for {screen_id}"
            last_req = captured_requests[-1]
            print(f"      Payload sent to backend: screen='{last_req.get('screen')}', message='{last_req.get('message')}'")
            assert last_req.get("screen") == screen_id, f"Expected screen='{screen_id}', got '{last_req.get('screen')}'"

            # Wait for AI response before moving to next tab
            if page.locator('text=Copilot формулирует ответ').is_visible():
                page.locator('text=Copilot формулирует ответ').wait_for(state="hidden", timeout=15000)

        results["copilot_screen_context_passing"] = True

        # Subtest 4.2: 1-Click Action Resolution Button Execution
        print("\n--- Subtest 4.2: 1-Click Action Resolution Button Execution ---")
        input_box = page.locator('aside input[type="text"], aside input')
        input_box.fill("Запустить проверку готовности к деплою")
        input_box.press("Enter")
        
        # Wait for action button to appear in AI response
        action_btn = page.locator('aside button[title^="Выполнить:"]').last
        action_btn.wait_for(state="visible", timeout=15000)
        assert action_btn.is_visible(), "1-Click action resolution button must be present in AI message bubble"
        btn_text = action_btn.text_content().strip()
        print(f"   Clicking 1-click action button: '{btn_text}'...")

        # Click the 1-click action button
        action_btn.click()
        page.wait_for_timeout(1000)

        # Verify action executed (DryRunModal opened, or Jobs view navigated, or toast notification displayed)
        modal_opened = page.locator('h2:has-text("Dry-Run")').is_visible()
        jobs_active = page.locator('h3:has-text("История Пайплайнов")').is_visible()
        toast_active = page.locator('div.fixed.bottom-6.right-6').is_visible()
        diff_active = page.locator('h2:has-text("Monaco Diff & LLM Guard")').is_visible()
        action_executed = modal_opened or jobs_active or toast_active or diff_active
        print(f"   1-Click action successfully triggered response: Modal={modal_opened}, Jobs={jobs_active}, Toast={toast_active}, Diff={diff_active}")
        assert action_executed, "Clicking 1-click action button in Copilot bubble must trigger the corresponding application workflow"
        results["copilot_1click_action_execution"] = True

        # Dismiss modal and Copilot
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # -------------------------------------------------------------
        # TEST SUITE 5: HIGH-DENSITY INVENTORY COLUMNS AUDIT (R3)
        # -------------------------------------------------------------
        print("\n=== STAGE 6: High-Density Inventory Inspection (R3) ===")
        page.locator('button:has-text("Устройства"), a:has-text("Устройства")').first.click()
        page.wait_for_timeout(500)

        table_headers = [h.strip() for h in page.locator('table th').all_text_contents() if h.strip()]
        print(f"   Table headers: {table_headers}")
        assert "Oper Status" in table_headers, "Table must contain separate Oper Status column"
        assert "Intent Status" in table_headers, "Table must contain separate Intent Status column"

        sparklines = page.locator('.sparkline').count()
        timelines = page.locator('.state-timeline').count()
        card_btns = page.locator('table tbody tr button:has-text("Карточка")').count()
        diff_btns = page.locator('table tbody tr button:has-text("Diff")').count()
        dryrun_btns = page.locator('table tbody tr button:has-text("Dry-run")').count()
        print(f"   Sparklines: {sparklines}, Timelines: {timelines}, Actions: Карточка={card_btns}, Diff={diff_btns}, Dry-run={dryrun_btns}")
        assert sparklines >= 4, "Expected micro-sparklines in device table"
        assert timelines >= 4, "Expected 24h state timelines in device table"
        assert card_btns >= 4 and diff_btns >= 4 and dryrun_btns >= 4, "Expected 1-click action buttons on every row"
        results["high_density_inventory"] = True

        # -------------------------------------------------------------
        # FINAL LOG AUDIT: CONSOLE & PAGE ERRORS
        # -------------------------------------------------------------
        print("\n=== STAGE 7: Console & Page Error Audit ===")
        print(f"Total Console errors: {len(console_errors)}")
        for err in console_errors:
            print(f"  [CONSOLE ERROR] {err}")
        print(f"Total Page errors: {len(page_errors)}")
        for err in page_errors:
            print(f"  [PAGE ERROR] {err}")

        results["zero_console_errors"] = (len(console_errors) == 0)
        results["zero_page_errors"] = (len(page_errors) == 0)

        browser.close()

    print("\n================ FINAL RESULTS SUMMARY ================")
    all_passed = True
    for k, v in results.items():
        status = "PASSED" if v else "FAILED"
        print(f"  - {k}: {status}")
        if not v:
            all_passed = False

    print(f"\nOVERALL PROBE VERDICT: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    return all_passed

if __name__ == "__main__":
    success = run_probe()
    sys.exit(0 if success else 1)
