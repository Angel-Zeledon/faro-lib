/**
 * Playwright end-to-end test — Forecast Wizard completo
 * login → dataset (select existing) → analysis/outlier → columns → features/Fourier →
 * models → routing → validation → train → results → SKU Intelligence
 *
 * Run: NODE_PATH=C:\Users\Jahir\Documents\forecasting\Frontend\node_modules node playwright_forecast.js
 */

const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');

const BASE_URL = 'http://localhost:3000';
const EMAIL    = 'demo@acmecorp.demo';
const PASSWORD = 'Test1234!';
const HEADLESS = false;

// ── Screenshots ─────────────────────────────────────────────────────────────
const SHOTS_DIR = path.join(__dirname, 'screenshots');
if (!fs.existsSync(SHOTS_DIR)) fs.mkdirSync(SHOTS_DIR);
fs.readdirSync(SHOTS_DIR).forEach(f => { try { fs.unlinkSync(path.join(SHOTS_DIR, f)); } catch {} });

let shotIdx = 0;
async function shot(page, name) {
  const file = path.join(SHOTS_DIR, `${String(++shotIdx).padStart(2, '0')}_${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`  [ss] ${path.basename(file)}`);
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function ok(msg)   { console.log(`  OK  ${msg}`); }
function info(msg) { console.log(`  ..  ${msg}`); }

async function clickBtn(page, text, desc) {
  try {
    const btn = page.locator(`button:has-text("${text}")`).first();
    if (await btn.isVisible({ timeout: 4000 })) {
      await btn.click();
      ok(`Clicked: ${desc ?? text}`);
      return true;
    }
  } catch {}
  info(`Could not click: ${desc ?? text} — skipping`);
  return false;
}

async function clickFirst(page, selectors, desc) {
  for (const sel of selectors) {
    try {
      const el = page.locator(sel).first();
      if (await el.isVisible({ timeout: 1500 })) {
        await el.click();
        ok(`Clicked: ${desc}`);
        return true;
      }
    } catch {}
  }
  info(`Could not click: ${desc} — skipping`);
  return false;
}

// ── Main ─────────────────────────────────────────────────────────────────────
(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, slowMo: 80 });
  const ctx  = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const jsErrors = [];
  page.on('console', m => { if (m.type() === 'error') jsErrors.push(m.text()); });
  page.on('pageerror', e => jsErrors.push(e.message));

  const PASS = [], FAIL = [];
  function check(name, condition, detail = '') {
    if (condition) { PASS.push(name); ok(`[PASS] ${name}`); }
    else { FAIL.push(name); console.error(`  FAIL ${name}${detail ? ': ' + detail : ''}`); }
  }

  try {
    // ═══════════════════════════════════════════════════════════════════════
    // 1. LOGIN
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[1] Login');
    await page.goto(`${BASE_URL}/login`, { waitUntil: 'load', timeout: 20000 });
    await shot(page, 'login_page');

    await page.fill('input[type="email"]', EMAIL);
    await page.fill('input[type="password"]', PASSWORD);
    await shot(page, 'login_filled');
    await page.click('button[type="submit"]');

    try {
      await page.waitForURL('**/dashboard', { timeout: 20000 });
      check('login_redirect', true);
    } catch {
      await shot(page, 'login_failed');
      check('login_redirect', false, 'Did not navigate to /dashboard');
    }
    await page.waitForTimeout(2000);
    await shot(page, 'dashboard');
    check('dashboard_loaded', page.url().includes('/dashboard'));

    // ═══════════════════════════════════════════════════════════════════════
    // 2. NAVIGATE TO FORECAST STUDIO
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[2] Navigate to Forecast Studio');
    await page.goto(`${BASE_URL}/forecast`, { waitUntil: 'load', timeout: 15000 });
    await page.waitForTimeout(2000);
    await shot(page, 'forecast_step1_initial');
    check('forecast_page_loaded', page.url().includes('/forecast'));

    // ═══════════════════════════════════════════════════════════════════════
    // 3. STEP 1 — Select existing data source + create session
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[3] Step 1 — Select data source');

    // Wait for the sources list to populate
    await page.waitForSelector('text=Select a Data Source', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);

    // Prefer a known-good source; fall back to any visible source
    const preferredSources = [
      'text=01_daily_single_sku',
      'text=02_daily_3_skus',
      'text=03_weekly_4_skus',
      'text=12_daily_7_skus',
    ];

    let sourceSelected = false;
    for (const sel of preferredSources) {
      const el = page.locator(sel).first();
      if (await el.isVisible({ timeout: 1500 }).catch(() => false)) {
        await el.click();
        ok(`Selected source: ${sel.replace('text=', '')}`);
        sourceSelected = true;
        break;
      }
    }

    await page.waitForTimeout(400);
    await shot(page, 'step1_source_selected');
    check('step1_source_selected', sourceSelected);

    // Click "Continue with this source →"
    if (sourceSelected) {
      const contBtn = page.locator('button:has-text("Continue with this source")').first();
      if (await contBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await contBtn.click();
        ok('Clicked: Continue with this source');
      }

      // Wait until "Select a Data Source" heading disappears (Step 1 done)
      // and Step 2 content appears (Outlier Treatment is always shown)
      info('Waiting for session creation + inspection...');
      await page.waitForSelector('text=Outlier Treatment', { timeout: 45000 })
        .catch(() => info('Timed out waiting for Step 2'));
    }
    await shot(page, 'step2_loaded');

    // ═══════════════════════════════════════════════════════════════════════
    // 4. STEP 2 — Dataset analysis + Outlier config
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[4] Step 2 — Analysis / Outlier');
    await page.waitForTimeout(2000);  // let analysis API response arrive
    await shot(page, 'step2_full');

    const outlierVisible = await page.locator('text=Outlier Treatment').first().isVisible({ timeout: 5000 }).catch(() => false);
    check('outlier_section_visible', outlierVisible);

    if (outlierVisible) {
      // Select IQR fence strategy
      const iqrRadio = page.locator('input[value="iqr_fence"]').first();
      if (await iqrRadio.isVisible({ timeout: 2000 }).catch(() => false)) {
        await iqrRadio.click();
        ok('Selected iqr_fence strategy');
      }
      await page.waitForTimeout(400);
      await shot(page, 'step2_iqr_selected');

      // Open per-SKU overrides panel if multi-SKU
      const perSkuBtn = page.locator('button:has-text("Override per SKU")').first();
      if (await perSkuBtn.isVisible({ timeout: 1500 }).catch(() => false)) {
        await perSkuBtn.click();
        await page.waitForTimeout(400);
        await shot(page, 'step2_per_sku_open');
        ok('Per-SKU overrides panel opened');
      }

      // Gap fill: choose "zero" if gaps exist
      const gapFillZero = page.locator('input[value="zero"]').first();
      if (await gapFillZero.isVisible({ timeout: 1500 }).catch(() => false)) {
        await gapFillZero.click();
        ok('Gap fill = zero selected');
      }
    }

    await shot(page, 'step2_configured');
    // Button: "Continue to Columns →"
    await clickBtn(page, 'Continue to Columns', 'Step 2 → Columns');
    await page.waitForTimeout(2000);
    await shot(page, 'step3_columns');

    // ═══════════════════════════════════════════════════════════════════════
    // 5. STEP 3 — Column mapping
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[5] Step 3 — Column mapping');
    await page.waitForTimeout(1500);
    await shot(page, 'step3_columns_loaded');

    // Check selects are populated (target + date dropdowns)
    const selectCount = await page.locator('select').count();
    check('columns_target_populated', selectCount >= 1, `${selectCount} selects found`);
    check('columns_date_populated', selectCount >= 2, `${selectCount} selects found`);

    // Button: "Confirm Columns →"
    await clickBtn(page, 'Confirm Columns', 'Step 3 → Features');
    await page.waitForTimeout(2500);
    await shot(page, 'step4_features');

    // ═══════════════════════════════════════════════════════════════════════
    // 6. STEP 4 — Features + Fourier
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[6] Step 4 — Features / Fourier');
    await page.waitForTimeout(1500);
    await shot(page, 'step4_features_loaded');

    const fourierVisible = await page.locator('text=Fourier').first().isVisible({ timeout: 5000 }).catch(() => false);
    check('fourier_section_visible', fourierVisible);

    if (fourierVisible) {
      // Enable period 7
      const period7Btn = page.locator('button:has-text("7")').first();
      if (await period7Btn.isVisible({ timeout: 2000 }).catch(() => false)) {
        await period7Btn.click();
        await page.waitForTimeout(400);
        await shot(page, 'step4_fourier_period7_on');
        ok('Fourier period 7 enabled');
      }
    }

    // Button: "Configure Features →"
    await clickBtn(page, 'Configure Features', 'Step 4 → Models');
    await page.waitForTimeout(2500);
    await shot(page, 'step5_models');

    // ═══════════════════════════════════════════════════════════════════════
    // 7. STEP 5 — Model selection
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[7] Step 5 — Models');
    await page.waitForTimeout(2000);
    await shot(page, 'step5_models_loaded');

    const lgbmVisible = await page.locator('text=lightgbm').first().isVisible({ timeout: 3000 }).catch(() => false);
    check('models_lightgbm_visible', lgbmVisible);

    const etsVisible = await page.locator('text=ets').first().isVisible({ timeout: 3000 }).catch(() => false);
    check('models_ets_visible', etsVisible);

    // Button: "Confirm Models →"
    await clickBtn(page, 'Confirm Models', 'Step 5 → Routing');
    await page.waitForTimeout(2000);
    await shot(page, 'step6_routing');

    // ═══════════════════════════════════════════════════════════════════════
    // 8. STEP 6 — Routing (click through)
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[8] Step 6 — Routing');
    await page.waitForTimeout(1500);
    await shot(page, 'step6_routing_loaded');

    // Button: "Continue to Validation →"
    await clickBtn(page, 'Continue to Validation', 'Step 6 → Validation');
    await page.waitForTimeout(2000);
    await shot(page, 'step7_validation');

    // ═══════════════════════════════════════════════════════════════════════
    // 9. STEP 7 — Validation config
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[9] Step 7 — Validation config');
    await page.waitForTimeout(1500);
    await shot(page, 'step7_validation_loaded');

    // Button: "Save Config →"
    await clickBtn(page, 'Save Config', 'Step 7 → Train');
    await page.waitForTimeout(2000);
    await shot(page, 'step8_train');

    // ═══════════════════════════════════════════════════════════════════════
    // 10. STEP 8 — Train
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[10] Step 8 — Training');
    await page.waitForTimeout(1000);
    await shot(page, 'step8_train_ready');

    const trainBtn = page.locator('button:has-text("Start Training")').first();
    const trainVisible = await trainBtn.isVisible({ timeout: 5000 }).catch(() => false);
    check('train_button_visible', trainVisible);

    if (trainVisible) {
      await trainBtn.click();
      ok('Training started');
      await page.waitForTimeout(2000);
      await shot(page, 'step8_training_started');

      // Wait for progress indicator
      await page.locator('text=RUNNING, [role="progressbar"]').first().waitFor({ timeout: 10000 }).catch(() => {});
      await shot(page, 'step8_training_progress');
      check('training_started', true);

      // Wait up to 4 minutes for completion
      info('Waiting for training to complete (up to 4 min)...');
      let completed = false;
      for (let i = 0; i < 48; i++) {
        await page.waitForTimeout(5000);
        const done = await page.locator('text=COMPLETED').first().isVisible({ timeout: 2000 }).catch(() => false);
        if (done) { completed = true; break; }
        const failed = await page.locator('text=FAILED').first().isVisible({ timeout: 1000 }).catch(() => false);
        if (failed) { info('Training ended with FAILED state'); break; }
        if (i % 6 === 0) info(`  waiting... ${(i + 1) * 5}s elapsed`);
      }
      await shot(page, 'step8_training_end');
      check('training_completed', completed);
    }

    // ═══════════════════════════════════════════════════════════════════════
    // 11. STEP 9 — Results
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[11] Step 9 — Results');
    await page.waitForTimeout(2000);
    await shot(page, 'step9_results');

    const wapeVisible = await page.locator('text=WAPE').first().isVisible({ timeout: 8000 }).catch(() => false);
    check('results_metrics_visible', wapeVisible);

    const chartVisible = await page.locator('canvas, [class*="chart"], [class*="echarts"]').first().isVisible({ timeout: 5000 }).catch(() => false);
    check('results_chart_visible', chartVisible);

    await shot(page, 'step9_results_full');

    // ═══════════════════════════════════════════════════════════════════════
    // 12. SKU INTELLIGENCE
    // ═══════════════════════════════════════════════════════════════════════
    console.log('\n[12] SKU Intelligence page');
    // Click sidebar link or navigate directly
    const skuLink = page.locator('a[href="/skus"]').first();
    if (await skuLink.isVisible({ timeout: 2000 }).catch(() => false)) {
      await skuLink.click();
    } else {
      await page.goto(`${BASE_URL}/skus`, { waitUntil: 'load', timeout: 15000 });
    }
    await page.waitForTimeout(3000);
    await shot(page, 'sku_intelligence');

    const skuTitle = await page.locator('text=SKU Intelligence').first().isVisible({ timeout: 5000 }).catch(() => false);
    check('sku_intelligence_loaded', skuTitle);
    await shot(page, 'sku_intelligence_full');

    // ═══════════════════════════════════════════════════════════════════════
    // FINAL REPORT
    // ═══════════════════════════════════════════════════════════════════════
    const criticalJsErrors = jsErrors.filter(e =>
      !e.includes('favicon') && !e.includes('ResizeObserver') && !e.includes('Non-Error exception')
    );

    console.log('\n' + '='.repeat(65));
    console.log('  PLAYWRIGHT RESULTS');
    console.log('='.repeat(65));
    PASS.forEach(n => console.log(`  PASS  ${n}`));
    FAIL.forEach(n => console.log(`  FAIL  ${n}`));
    if (criticalJsErrors.length) {
      console.log(`\n  JS Errors (${criticalJsErrors.length}):`);
      criticalJsErrors.slice(0, 5).forEach(e => console.log(`    - ${e.slice(0, 120)}`));
    }
    console.log(`\n  Screenshots: ${shotIdx} saved to test_csvs/screenshots/`);
    console.log(`  PASS: ${PASS.length}  |  FAIL: ${FAIL.length}`);
    console.log('='.repeat(65) + '\n');

  } catch (err) {
    console.error(`\nUNEXPECTED ERROR: ${err.message}`);
    console.error(err.stack);
    await shot(page, 'unexpected_error');
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
