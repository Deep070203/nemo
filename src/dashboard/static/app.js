/**
 * NEMO: Real-Time Pump.fun Forensics & Screener HUD
 * Pure Vanilla JavaScript Client
 */

(function () {
  'use strict';

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // State Management
  const state = {
    tokens: new Map(), // mint -> token object
    trades: [],        // array of trade objects
    audits: new Map(), // mint -> report object
    activeMint: null,
    filterTier: 'all',
    searchQuery: '',
    wsConnected: false
  };

  // DOM Cache
  const dom = {
    streamStatus: document.getElementById('streamStatus'),
    statusText: document.getElementById('statusText'),
    statTokens: document.getElementById('statTokensCount'),
    statTrades: document.getElementById('statTradesCount'),
    statRugs: document.getElementById('statRugsCount'),
    statRiskRatio: document.getElementById('statRiskRatio'),
    tokenSearchInput: document.getElementById('tokenSearchInput'),
    filterChips: document.querySelectorAll('.chip'),
    tokenStreamContainer: document.getElementById('tokenStreamContainer'),
    liveTokenCounter: document.getElementById('liveTokenCounter'),

    // Inspector
    selectedMintTag: document.getElementById('selectedMintTag'),
    btnCopyMint: document.getElementById('btnCopyMint'),
    btnPumpLink: document.getElementById('btnPumpLink'),
    btnRunAudit: document.getElementById('btnRunAudit'),
    btnViewSurvival: document.getElementById('btnViewSurvival'),
    inspectorPlaceholder: document.getElementById('inspectorPlaceholder'),
    inspectorContent: document.getElementById('inspectorContent'),
    inspectorAvatar: document.getElementById('inspectorAvatar'),
    inspectorName: document.getElementById('inspectorName'),
    inspectorSymbol: document.getElementById('inspectorSymbol'),
    inspectorCreatedAt: document.getElementById('inspectorCreatedAt'),
    riskGaugeCircle: document.getElementById('riskGaugeCircle'),
    riskScoreVal: document.getElementById('riskScoreVal'),
    riskTierBadge: document.getElementById('riskTierBadge'),
    inspectorFlagsList: document.getElementById('inspectorFlagsList'),

    // Inspector Metric Quadrants
    valBlock0Supply: document.getElementById('valBlock0Supply'),
    valJitoStatus: document.getElementById('valJitoStatus'),
    valSlot: document.getElementById('valSlot'),
    valUniqueBlock0Buyers: document.getElementById('valUniqueBlock0Buyers'),
    valHHI: document.getElementById('valHHI'),
    valGini: document.getElementById('valGini'),
    valClusterShare: document.getElementById('valClusterShare'),
    valDistTier: document.getElementById('valDistTier'),
    valVPIN: document.getElementById('valVPIN'),
    valEntropy: document.getElementById('valEntropy'),
    valAutocorr: document.getElementById('valAutocorr'),
    valWashStatus: document.getElementById('valWashStatus'),
    valPhash: document.getElementById('valPhash'),
    valTwitter: document.getElementById('valTwitter'),
    valTelegram: document.getElementById('valTelegram'),
    valSocialScore: document.getElementById('valSocialScore'),

    // Universal Coin Inspector
    inspectCoinInput: document.getElementById('inspectCoinInput'),
    btnInspectCoin: document.getElementById('btnInspectCoin'),
    btnInspectClear: document.getElementById('btnInspectClear'),
    evmDiagnosticBanner: document.getElementById('evmDiagnosticBanner'),
    evmNetworkTag: document.getElementById('evmNetworkTag'),
    evmExplanationText: document.getElementById('evmExplanationText'),
    evmContractCode: document.getElementById('evmContractCode'),
    evmAssetName: document.getElementById('evmAssetName'),

    // ML & Survival Card
    mlPredictionCard: document.getElementById('mlPredictionCard'),
    mlVerdictTag: document.getElementById('mlVerdictTag'),
    mlRugProb: document.getElementById('mlRugProb'),
    mlRiskTier: document.getElementById('mlRiskTier'),
    mlHazardMult: document.getElementById('mlHazardMult'),
    mlHazardInterp: document.getElementById('mlHazardInterp'),
    mlFeaturesRow: document.getElementById('mlFeaturesRow'),

    // Right Tabs
    tabTradeTape: document.getElementById('tabTradeTape'),
    tabSurvival: document.getElementById('tabSurvival'),
    viewTradeTape: document.getElementById('viewTradeTape'),
    viewSurvival: document.getElementById('viewSurvival'),
    tradeTapeContainer: document.getElementById('tradeTapeContainer'),
    hazardRatiosList: document.getElementById('hazardRatiosList'),

    // Top View Mode Switcher
    btnViewLiveStream: document.getElementById('btnViewLiveStream'),
    btnViewCohorts: document.getElementById('btnViewCohorts'),
    streamDashboardView: document.getElementById('streamDashboardView'),
    cohortDashboardView: document.getElementById('cohortDashboardView'),
    cohortPendingBadge: document.getElementById('cohortPendingBadge'),

    // Cohort Summary & Actions
    statCohortTotal: document.getElementById('statCohortTotal'),
    statCohortRugs: document.getElementById('statCohortRugs'),
    statCohortSurvivors: document.getElementById('statCohortSurvivors'),
    statCohortReaudit: document.getElementById('statCohortReaudit'),
    statCohortGraduated: document.getElementById('statCohortGraduated'),
    statCohortCTO: document.getElementById('statCohortCTO'),
    statCohortPrecision: document.getElementById('statCohortPrecision'),
    btnRunCohortBatch: document.getElementById('btnRunCohortBatch'),
    btnReloadRugPrices: document.getElementById('btnReloadRugPrices'),
    btnReloadRugPricesInner: document.getElementById('btnReloadRugPricesInner'),
    btnReloadSurvivorPrices: document.getElementById('btnReloadSurvivorPrices'),

    // Cohort Tabs & Panes
    tabBtnRugs: document.getElementById('tabBtnRugs'),
    tabBtnSurvivors: document.getElementById('tabBtnSurvivors'),
    tabBtnReaudit: document.getElementById('tabBtnReaudit'),
    tabBtnLearning: document.getElementById('tabBtnLearning'),
    countTabRugs: document.getElementById('countTabRugs'),
    countTabSurvivors: document.getElementById('countTabSurvivors'),
    countTabReaudit: document.getElementById('countTabReaudit'),
    paneRugs: document.getElementById('paneRugs'),
    paneSurvivors: document.getElementById('paneSurvivors'),
    paneReaudit: document.getElementById('paneReaudit'),
    paneLearning: document.getElementById('paneLearning'),

    // Cohort Tables & Containers
    rugsTableBody: document.getElementById('rugsTableBody'),
    survivorsTableBody: document.getElementById('survivorsTableBody'),
    reauditCardsContainer: document.getElementById('reauditCardsContainer'),
    rugsAlertBanner: document.getElementById('rugsAlertBanner'),

    // Learning Elements
    matrixTP: document.getElementById('matrixTP'),
    matrixFP: document.getElementById('matrixFP'),
    matrixFN: document.getElementById('matrixFN'),
    matrixTN: document.getElementById('matrixTN'),
    cellMatrixTP: document.getElementById('cellMatrixTP'),
    cellMatrixFP: document.getElementById('cellMatrixFP'),
    cellMatrixFN: document.getElementById('cellMatrixFN'),
    cellMatrixTN: document.getElementById('cellMatrixTN'),
    tabMatrixTP: document.getElementById('tabMatrixTP'),
    tabMatrixFP: document.getElementById('tabMatrixFP'),
    tabMatrixFN: document.getElementById('tabMatrixFN'),
    tabMatrixTN: document.getElementById('tabMatrixTN'),
    countMatrixTabTP: document.getElementById('countMatrixTabTP'),
    countMatrixTabFP: document.getElementById('countMatrixTabFP'),
    countMatrixTabFN: document.getElementById('countMatrixTabFN'),
    countMatrixTabTN: document.getElementById('countMatrixTabTN'),
    matrixExplorerTitle: document.getElementById('matrixExplorerTitle'),
    matrixExplorerHint: document.getElementById('matrixExplorerHint'),
    matrixCohortTableBody: document.getElementById('matrixCohortTableBody'),
    lblAccuracy: document.getElementById('lblAccuracy'),
    lblPrecision: document.getElementById('lblPrecision'),
    lblRecall: document.getElementById('lblRecall'),
    ruleAttributionBody: document.getElementById('ruleAttributionBody'),
    topFPTableBody: document.getElementById('topFPTableBody'),
    topFNTableBody: document.getElementById('topFNTableBody'),

    // Paper Trading View Elements
    btnViewPaperTrading: document.getElementById('btnViewPaperTrading'),
    paperTradingView: document.getElementById('paperTradingView'),
    paperActiveBadge: document.getElementById('paperActiveBadge'),
    scalpPortfolioVal: document.getElementById('scalpPortfolioVal'),
    scalpCashVal: document.getElementById('scalpCashVal'),
    scalpRealizedPnl: document.getElementById('scalpRealizedPnl'),
    scalpReturnPct: document.getElementById('scalpReturnPct'),
    scalpWinRate: document.getElementById('scalpWinRate'),
    scalpOpenCount: document.getElementById('scalpOpenCount'),
    freerollPortfolioVal: document.getElementById('freerollPortfolioVal'),
    freerollCashVal: document.getElementById('freerollCashVal'),
    freerollRealizedPnl: document.getElementById('freerollRealizedPnl'),
    freerollReturnPct: document.getElementById('freerollReturnPct'),
    freerollMoonbags: document.getElementById('freerollMoonbags'),
    freerollOpenCount: document.getElementById('freerollOpenCount'),
    paperTotalPortfolio: document.getElementById('paperTotalPortfolio'),
    paperTotalRealizedPnl: document.getElementById('paperTotalRealizedPnl'),
    activePositionsCountBadge: document.getElementById('activePositionsCountBadge'),
    paperPositionsTableBody: document.getElementById('paperPositionsTableBody'),
    totalTradesBadge: document.getElementById('totalTradesBadge'),
    paperTradesTableBody: document.getElementById('paperTradesTableBody'),
  };

  // Initialize
  function init() {
    setupWebSocket();
    setupEventListeners();
    fetchStats();
    setInterval(fetchStats, 8000);
  }

  // =========================================================================
  // WebSocket Connection with Auto-Reconnect
  // =========================================================================
  let ws = null;
  let reconnectDelay = 1000;

  function setupWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;

    updateStatus(false, 'CONNECTING STREAM');
    ws = new WebSocket(wsUrl);

    ws.onopen = function () {
      reconnectDelay = 1000;
      updateStatus(true, 'LIVE STREAMING');
    };

    ws.onmessage = function (e) {
      try {
        const payload = JSON.parse(e.data);
        handleMessage(payload);
      } catch (err) {
        console.error('Error parsing WS message:', err);
      }
    };

    ws.onclose = function () {
      updateStatus(false, 'DISCONNECTED (RETRYING)');
      setTimeout(setupWebSocket, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
    };

    ws.onerror = function (err) {
      console.warn('WS error:', err);
      ws.close();
    };
  }

  function updateStatus(connected, text) {
    state.wsConnected = connected;
    dom.statusText.textContent = text;
    if (connected) {
      dom.streamStatus.classList.remove('connecting');
    } else {
      dom.streamStatus.classList.add('connecting');
    }
  }

  // =========================================================================
  // Inbound Message Dispatcher
  // =========================================================================
  function handleMessage(msg) {
    if (!msg || !msg.type) return;

    switch (msg.type) {
      case 'snapshot':
        if (msg.tokens) {
          msg.tokens.forEach(t => state.tokens.set(t.mint, t));
        }
        if (msg.trades) {
          state.trades = msg.trades;
        }
        if (msg.reports) {
          msg.reports.forEach(r => state.audits.set(r.mint, r));
        }
        renderTokenList();
        renderTradeTape();
        // Auto-select first token if none selected
        if (!state.activeMint && state.tokens.size > 0) {
          const firstMint = state.tokens.keys().next().value;
          selectToken(firstMint);
        }
        break;

      case 'token_created':
        const tok = msg.data;
        state.tokens.set(tok.mint, tok);
        renderTokenList();
        // Auto select if first token
        if (!state.activeMint) {
          selectToken(tok.mint);
        }
        break;

      case 'trade_executed':
        const trade = msg.data;
        state.trades.unshift(trade);
        if (state.trades.length > 50) state.trades.pop();
        prependTradeItem(trade);
        break;

      case 'audit_completed':
        const report = msg.data;
        state.audits.set(report.mint, report);
        // Refresh token card badge
        updateTokenCardBadge(report.mint, report);
        // Refresh inspector if currently open
        if (state.activeMint === report.mint) {
          renderInspector(state.tokens.get(report.mint), report);
        }
        break;

      case 'paper_trade_executed':
        if (dom.paperTradingView && dom.paperTradingView.style.display !== 'none') {
          loadPaperTradingData();
        } else {
          // Update the pill badge count
          fetch('/api/paper-trading/summary').then(r => r.json()).then(s => {
            if (dom.paperActiveBadge) dom.paperActiveBadge.textContent = s.active_positions_count || 0;
          }).catch(() => {});
        }
        break;

      case 'token_demoted':
        // If viewing cohort tables, refresh to reflect the token moving from Survivors to Rugs
        if (dom.cohortDashboardView && dom.cohortDashboardView.style.display !== 'none') {
          fetchCohortSummary();
          switchCohortBucketTab(activeCohortBucket);
        }
        // Update paper trading UI if open
        if (dom.paperTradingView && dom.paperTradingView.style.display !== 'none') {
          loadPaperTradingData();
        }
        break;
    }
  }

  // =========================================================================
  // Token Stream Rendering
  // =========================================================================
  function renderTokenList() {
    const container = dom.tokenStreamContainer;
    const tokensArray = Array.from(state.tokens.values()).reverse();

    const filtered = tokensArray.filter(t => {
      const audit = state.audits.get(t.mint);
      const tier = audit ? audit.risk_tier : 'LOW';

      if (state.filterTier === 'clean' && tier !== 'LOW') return false;
      if (state.filterTier === 'medium' && tier !== 'MEDIUM') return false;
      if (state.filterTier === 'critical' && (tier !== 'HIGH' && tier !== 'CRITICAL')) return false;

      if (state.searchQuery) {
        const q = state.searchQuery.toLowerCase();
        const sym = (t.symbol || '').toLowerCase();
        const name = (t.name || '').toLowerCase();
        const mint = (t.mint || '').toLowerCase();
        if (!sym.includes(q) && !name.includes(q) && !mint.includes(q)) return false;
      }
      return true;
    });

    dom.liveTokenCounter.textContent = `${filtered.length} tokens`;

    if (filtered.length === 0) {
      container.innerHTML = '<div class="loading-state"><span>No tokens match active filter</span></div>';
      return;
    }

    container.innerHTML = '';
    filtered.forEach(tok => {
      const card = createTokenCard(tok);
      container.appendChild(card);
    });
  }

  function createTokenCard(tok) {
    const card = document.createElement('div');
    card.className = `token-card ${state.activeMint === tok.mint ? 'active' : ''}`;
    card.id = `card-${tok.mint}`;

    const audit = state.audits.get(tok.mint);
    const tier = audit ? audit.risk_tier : (tok.max_severity || 'LOW');
    const score = audit ? audit.composite_risk_score : 0;
    const devBuyPct = tok.initial_buy_supply_pct ? tok.initial_buy_supply_pct.toFixed(1) : '0.0';

    card.innerHTML = `
      <div class="token-card-top">
        <div>
          <span class="tok-symbol">${tok.symbol || '???'}</span>
          <span class="tok-name">${tok.name || 'Unnamed Token'}</span>
        </div>
        <span class="tok-risk-pill risk-${tier}">${tier} (${score})</span>
      </div>
      <div class="token-card-body">
        <span>Dev Buy: <strong>${devBuyPct}%</strong></span>
        <span>SOL: <strong>${(tok.sol_amount || 0).toFixed(2)}</strong></span>
        <span class="mono">${tok.mint.substring(0, 4)}..${tok.mint.substring(tok.mint.length - 3)}</span>
      </div>
      <div class="token-card-badges">
        ${renderCardFlags(audit ? audit.all_flags : (tok.flags || []))}
      </div>
    `;

    card.addEventListener('click', () => selectToken(tok.mint));
    return card;
  }

  function renderCardFlags(flags) {
    if (!flags || flags.length === 0) {
      return '<span class="tag-badge">clean launch</span>';
    }
    return flags.slice(0, 2).map(f => {
      const isAlert = f.includes('JITO') || f.includes('BLOCK0') || f.includes('SYBIL') || f.includes('CLONE');
      const label = f.split('(')[0].replace(/_/g, ' ').toLowerCase();
      return `<span class="tag-badge ${isAlert ? 'alert' : ''}">${label}</span>`;
    }).join('');
  }

  function updateTokenCardBadge(mint, report) {
    const card = document.getElementById(`card-${mint}`);
    if (!card) return;

    const pill = card.querySelector('.tok-risk-pill');
    if (pill) {
      pill.className = `tok-risk-pill risk-${report.risk_tier}`;
      pill.textContent = `${report.risk_tier} (${report.composite_risk_score})`;
    }

    const badgesContainer = card.querySelector('.token-card-badges');
    if (badgesContainer) {
      badgesContainer.innerHTML = renderCardFlags(report.all_flags);
    }
  }

  // =========================================================================
  // Center Inspector
  // =========================================================================
  function selectToken(mint) {
    state.activeMint = mint;

    // Highlight active card
    document.querySelectorAll('.token-card').forEach(c => c.classList.remove('active'));
    const currentCard = document.getElementById(`card-${mint}`);
    if (currentCard) currentCard.classList.add('active');

    const tok = state.tokens.get(mint);
    if (!tok) return;

    const audit = state.audits.get(mint);
    renderInspector(tok, audit);
  }

  function renderInspector(tok, audit, mlInfo = null, survivalInfo = null, features = null) {
    dom.inspectorPlaceholder.style.display = 'none';
    dom.inspectorContent.style.display = 'block';

    dom.selectedMintTag.textContent = `${tok.mint.substring(0, 8)}...${tok.mint.substring(tok.mint.length - 6)}`;
    dom.btnCopyMint.style.display = 'inline-block';
    dom.btnPumpLink.style.display = 'inline-block';
    dom.btnPumpLink.href = `https://pump.fun/${tok.mint}`;

    dom.inspectorName.textContent = tok.name || 'Unnamed Token';
    dom.inspectorSymbol.textContent = `$${tok.symbol || '???'}`;
    dom.inspectorCreatedAt.textContent = tok.created_at ? new Date(tok.created_at).toLocaleTimeString() : 'Recent';

    // Avatar preview
    if (tok.uri && tok.uri.includes('ipfs')) {
      const httpUrl = tok.uri.replace('ipfs://', 'https://ipfs.io/ipfs/');
      dom.inspectorAvatar.innerHTML = `<img src="${httpUrl}" onerror="this.parentElement.textContent='?'">`;
    } else {
      dom.inspectorAvatar.textContent = (tok.symbol || '?')[0].toUpperCase();
    }

    // Risk Gauge
    const score = audit ? audit.composite_risk_score : 0;
    const tier = audit ? audit.risk_tier : 'LOW';

    dom.riskScoreVal.textContent = score;
    dom.riskGaugeCircle.className = `gauge-circle risk-${tier.toLowerCase().slice(0, 4)}`;
    dom.riskTierBadge.className = `risk-tier-badge risk-${tier}`;
    dom.riskTierBadge.textContent = `${tier} RISK`;

    // Flags Box
    const flags = audit ? audit.all_flags : [];
    if (flags && flags.length > 0) {
      dom.inspectorFlagsList.innerHTML = flags.map(f => `<span class="flag-pill">${f}</span>`).join('');
    } else {
      dom.inspectorFlagsList.innerHTML = `<span class="flag-pill clean">✓ No Malicious Heuristics Flagged</span>`;
    }

    // Quadrant 1: Block-0 & Jito
    if (audit && audit.bundle_analysis) {
      const b = audit.bundle_analysis;
      dom.valBlock0Supply.textContent = `${b.block0_supply_pct.toFixed(1)}%`;
      dom.valBlock0Supply.className = `m-val ${b.block0_supply_pct > 15 ? 'alert' : 'clean'}`;
      dom.valJitoStatus.textContent = b.is_jito_bundled ? `Jito Tip (${(b.total_jito_tip_lamports / 1e9).toFixed(3)} SOL)` : 'None';
      dom.valJitoStatus.className = `m-val ${b.is_jito_bundled ? 'alert' : 'clean'}`;
      dom.valSlot.textContent = b.slot || 'N/A';
      dom.valUniqueBlock0Buyers.textContent = b.unique_block0_buyers || 0;
    } else {
      dom.valBlock0Supply.textContent = tok.initial_buy_supply_pct ? `${tok.initial_buy_supply_pct.toFixed(1)}%` : '0.0%';
      dom.valJitoStatus.textContent = 'Analyzing...';
      dom.valSlot.textContent = 'Pending RPC';
      dom.valUniqueBlock0Buyers.textContent = '1';
    }

    // Quadrant 2: Sybil & Concentration
    if (audit && audit.concentration_metrics) {
      const c = audit.concentration_metrics;
      dom.valHHI.textContent = c.hhi_score.toFixed(0);
      dom.valHHI.className = `m-val ${c.hhi_score >= 2500 ? 'alert' : 'clean'}`;
      dom.valGini.textContent = c.gini_coefficient.toFixed(2);
      dom.valDistTier.textContent = c.distribution_tier;
    } else {
      dom.valHHI.textContent = '350';
      dom.valGini.textContent = '0.35';
      dom.valDistTier.textContent = 'Moderate';
    }

    if (audit && audit.graph_analysis) {
      const g = audit.graph_analysis;
      dom.valClusterShare.textContent = `${g.largest_cluster_share_pct.toFixed(1)}%`;
      dom.valClusterShare.className = `m-val ${g.largest_cluster_share_pct > 20 ? 'alert' : 'clean'}`;
    } else {
      dom.valClusterShare.textContent = '0.0%';
    }

    // Quadrant 3: Microstructure & Flow
    if (audit && audit.vpin_analysis) {
      const v = audit.vpin_analysis;
      dom.valVPIN.textContent = v.vpin_score.toFixed(2);
      dom.valVPIN.className = `m-val ${v.vpin_score >= 0.7 ? 'alert' : 'clean'}`;
    } else {
      dom.valVPIN.textContent = '0.18';
      dom.valVPIN.className = 'm-val clean';
    }

    if (audit && audit.entropy_analysis) {
      const e = audit.entropy_analysis;
      dom.valEntropy.textContent = e.shannon_entropy.toFixed(2);
      dom.valAutocorr.textContent = e.sign_autocorrelation.toFixed(2);
      dom.valWashStatus.textContent = e.is_wash_trading ? 'Wash Detected' : 'Organic';
      dom.valWashStatus.className = `m-val ${e.is_wash_trading ? 'alert' : 'clean'}`;
    } else {
      dom.valEntropy.textContent = '3.12';
      dom.valAutocorr.textContent = '0.05';
      dom.valWashStatus.textContent = 'Organic';
      dom.valWashStatus.className = 'm-val clean';
    }

    // Quadrant 4: Meme Clones & Socials
    if (audit && audit.image_match) {
      dom.valPhash.textContent = audit.image_match.is_clone ? 'Clone Detected' : 'Original';
      dom.valPhash.className = `m-val ${audit.image_match.is_clone ? 'alert' : 'clean'}`;
    } else {
      dom.valPhash.textContent = 'Original';
    }

    if (audit && audit.social_audit) {
      const s = audit.social_audit;
      dom.valTwitter.textContent = s.has_twitter ? (s.is_dummy_socials ? 'Dummy Link' : 'Active') : 'Missing';
      dom.valTelegram.textContent = s.has_telegram ? 'Active' : 'Missing';
      dom.valSocialScore.textContent = `${s.social_completeness_score}/100`;
    } else {
      dom.valTwitter.textContent = 'Pending';
      dom.valTelegram.textContent = 'Pending';
      dom.valSocialScore.textContent = '40/100';
    }

    // Machine Learning & Survival Prediction Card
    if (dom.mlPredictionCard) {
      if (mlInfo || survivalInfo) {
        dom.mlPredictionCard.style.display = 'block';
        if (mlInfo) {
          dom.mlRugProb.textContent = `${mlInfo.rug_probability}%`;
          dom.mlRiskTier.textContent = mlInfo.risk_tier;
          dom.mlRiskTier.className = `ml-val tier-tag ${mlInfo.risk_tier.toLowerCase()}`;
          dom.mlVerdictTag.textContent = mlInfo.verdict;
          dom.mlVerdictTag.className = `ml-verdict-tag ${mlInfo.predicted_label === 1 ? 'rug' : ''}`;
        } else {
          dom.mlRugProb.textContent = 'N/A';
          dom.mlRiskTier.textContent = tier;
          dom.mlVerdictTag.textContent = tier === 'CRITICAL' || tier === 'HIGH' ? 'RUG RISK' : 'VIABLE';
        }

        if (survivalInfo) {
          dom.mlHazardMult.textContent = `${survivalInfo.hazard_multiplier}x`;
          dom.mlHazardInterp.textContent = survivalInfo.interpretation;
        } else {
          dom.mlHazardMult.textContent = '1.00x';
          dom.mlHazardInterp.textContent = 'Baseline cohort dynamics';
        }

        if (features && dom.mlFeaturesRow) {
          dom.mlFeaturesRow.innerHTML = Object.entries(features).map(([k, v]) => {
            const isFlag = (k === 'dev_buy_supply_pct' && v > 10) || (k === 'vpin_score' && v > 0.4) || (k === 'is_jito_mev' && v === 1);
            return `<span class="ml-feat-chip ${isFlag ? 'flagged' : ''}">${k}: <strong>${typeof v === 'number' ? v.toFixed(3) : v}</strong></span>`;
          }).join('');
        }
      } else {
        dom.mlPredictionCard.style.display = 'none';
      }
    }
  }

  // =========================================================================
  // Right Panel: Trade Tape
  // =========================================================================
  function renderTradeTape() {
    const container = dom.tradeTapeContainer;
    container.innerHTML = '';
    state.trades.slice(0, 30).forEach(trade => {
      container.appendChild(createTradeItem(trade));
    });
  }

  function prependTradeItem(trade) {
    const container = dom.tradeTapeContainer;
    const item = createTradeItem(trade);
    container.insertBefore(item, container.firstChild);
    if (container.children.length > 40) {
      container.removeChild(container.lastChild);
    }
  }

  function createTradeItem(trade) {
    const item = document.createElement('div');
    item.className = 'tape-item';
    const isBuy = (trade.tx_type || trade.txType || 'buy').toLowerCase() === 'buy';
    const sol = (trade.sol_amount || trade.solAmount || 0).toFixed(3);
    const progress = trade.bonding_curve_progress_pct || 0;
    const mint = trade.mint || '';

    item.innerHTML = `
      <div class="tape-item-top">
        <span class="tape-badge ${isBuy ? 'buy' : 'sell'}">${isBuy ? 'BUY' : 'SELL'}</span>
        <span class="mono" style="color: var(--text-dim);">${mint.substring(0, 4)}..${mint.substring(mint.length - 3)}</span>
        <span class="tape-sol">${sol} SOL</span>
      </div>
      <div class="tape-progress-bar">
        <div class="tape-progress-fill" style="width: ${Math.min(100, Math.max(2, progress))}%;"></div>
      </div>
    `;
    return item;
  }

  // =========================================================================
  // Survival Model Fetcher
  // =========================================================================
  async function fetchSurvivalModel() {
    dom.hazardRatiosList.innerHTML = '<div class="loading-state"><div class="spinner"></div><span>Computing Cox hazard regression...</span></div>';
    try {
      const res = await fetch('/api/survival');
      const data = await res.json();

      if (data.status === 'insufficient_data') {
        dom.hazardRatiosList.innerHTML = `<div class="loading-state"><span>Insufficient token records (${data.count} found). Need >= 5 tokens to fit Cox model.</span></div>`;
        return;
      }

      if (data.hazard_ratios) {
        dom.hazardRatiosList.innerHTML = data.hazard_ratios.map(r => {
          const isDanger = r.hazard_ratio > 1.05;
          const isProtect = r.hazard_ratio < 0.95;
          const cls = isDanger ? 'danger' : (isProtect ? 'protective' : '');
          return `
            <div class="hr-item">
              <span class="hr-name">${r.feature_name}</span>
              <span class="hr-multiplier ${cls}">${r.hazard_ratio.toFixed(2)}x HR</span>
            </div>
          `;
        }).join('');
      }
    } catch (err) {
      dom.hazardRatiosList.innerHTML = `<div class="loading-state"><span>Error loading survival data: ${err.message}</span></div>`;
    }
  }

  // =========================================================================
  // Global Stats Fetcher
  // =========================================================================
  async function fetchStats() {
    try {
      const res = await fetch('/api/stats');
      const stats = await res.json();

      dom.statTokens.textContent = stats.total_tokens || 0;
      dom.statTrades.textContent = stats.total_trades || 0;
      dom.statRugs.textContent = stats.critical_rugs || 0;

      const total = stats.total_tokens || 0;
      const bad = (stats.critical_rugs || 0) + (stats.high_risk || 0);
      const ratio = total > 0 ? Math.round((bad / total) * 100) : 0;
      dom.statRiskRatio.textContent = `${ratio}%`;
    } catch (e) {
      console.debug('Stats fetch failed:', e);
    }
  }

  // =========================================================================
  // UI Event Handlers
  // =========================================================================
  function setupEventListeners() {
    // Search input
    dom.tokenSearchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value.trim();
      renderTokenList();
    });

    // Filter chips
    dom.filterChips.forEach(chip => {
      chip.addEventListener('click', () => {
        dom.filterChips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        state.filterTier = chip.getAttribute('data-filter');
        renderTokenList();
      });
    });

    // Copy Mint Button
    dom.btnCopyMint.addEventListener('click', () => {
      if (!state.activeMint) return;
      navigator.clipboard.writeText(state.activeMint);
      dom.btnCopyMint.textContent = '✓';
      setTimeout(() => { dom.btnCopyMint.textContent = '📋'; }, 1500);
    });

    // Re-Audit Button
    dom.btnRunAudit.addEventListener('click', async () => {
      if (!state.activeMint) return;
      dom.btnRunAudit.textContent = '⏳ Auditing...';
      try {
        const res = await fetch(`/api/audit/${state.activeMint}`, { method: 'POST' });
        const report = await res.json();
        state.audits.set(report.mint, report);
        renderInspector(state.tokens.get(report.mint), report);
        updateTokenCardBadge(report.mint, report);
      } catch (e) {
        alert('Audit failed: ' + e.message);
      } finally {
        dom.btnRunAudit.textContent = '⚡ Re-Audit Active';
      }
    });

    // Tab Switching
    dom.tabTradeTape.addEventListener('click', () => {
      dom.tabTradeTape.classList.add('active');
      dom.tabSurvival.classList.remove('active');
      dom.viewTradeTape.classList.add('active');
      dom.viewSurvival.classList.remove('active');
    });

    dom.tabSurvival.addEventListener('click', () => {
      dom.tabSurvival.classList.add('active');
      dom.tabTradeTape.classList.remove('active');
      dom.viewSurvival.classList.add('active');
      dom.viewTradeTape.classList.remove('active');
      fetchSurvivalModel();
    });

    // Survival View Button in Header
    dom.btnViewSurvival.addEventListener('click', () => {
      dom.tabSurvival.click();
    });

    // Top View Mode Switcher
    if (dom.btnViewLiveStream) dom.btnViewLiveStream.addEventListener('click', () => switchViewMode('stream'));
    if (dom.btnViewCohorts) dom.btnViewCohorts.addEventListener('click', () => switchViewMode('cohorts'));
    if (dom.btnViewPaperTrading) dom.btnViewPaperTrading.addEventListener('click', () => switchViewMode('papertrading'));

    // Cohort Tabs Switcher
    const cohortTabBtns = [dom.tabBtnRugs, dom.tabBtnSurvivors, dom.tabBtnReaudit, dom.tabBtnLearning];
    cohortTabBtns.forEach(btn => {
      if (!btn) return;
      btn.addEventListener('click', () => {
        cohortTabBtns.forEach(b => b && b.classList.remove('active'));
        btn.classList.add('active');
        const bucket = btn.getAttribute('data-bucket');
        switchCohortBucketTab(bucket);
      });
    });

    // Batch Audit & Price Reload Handlers
    if (dom.btnRunCohortBatch) {
      dom.btnRunCohortBatch.addEventListener('click', runCohortBatchAudit);
    }
    if (dom.btnReloadRugPrices) {
      dom.btnReloadRugPrices.addEventListener('click', () => reloadCohortPrices('rugs'));
    }
    if (dom.btnReloadRugPricesInner) {
      dom.btnReloadRugPricesInner.addEventListener('click', () => reloadCohortPrices('rugs'));
    }
    if (dom.btnReloadSurvivorPrices) {
      dom.btnReloadSurvivorPrices.addEventListener('click', () => reloadCohortPrices('survivors'));
    }

    // =======================================================================
    // Universal Coin Inspector Search Listeners
    // =======================================================================
    if (dom.btnInspectCoin) {
      dom.btnInspectCoin.addEventListener('click', () => {
        const addr = dom.inspectCoinInput ? dom.inspectCoinInput.value : '';
        inspectAddress(addr);
      });
    }

    if (dom.inspectCoinInput) {
      dom.inspectCoinInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          inspectAddress(dom.inspectCoinInput.value);
        }
      });

      dom.inspectCoinInput.addEventListener('input', (e) => {
        if (dom.btnInspectClear) {
          dom.btnInspectClear.style.display = e.target.value ? 'block' : 'none';
        }
      });
    }

    if (dom.btnInspectClear) {
      dom.btnInspectClear.addEventListener('click', () => {
        dom.inspectCoinInput.value = '';
        dom.btnInspectClear.style.display = 'none';
        if (dom.evmDiagnosticBanner) dom.evmDiagnosticBanner.style.display = 'none';
        dom.inspectCoinInput.focus();
      });
    }
  }

  // =========================================================================
  // Universal Coin Inspector Logic
  // =========================================================================
  async function inspectAddress(address) {
    if (!address || !address.trim()) return;
    const cleanAddr = address.trim();

    if (dom.btnInspectCoin) {
      dom.btnInspectCoin.disabled = true;
      dom.btnInspectCoin.textContent = '⏳ Inspecting...';
    }
    if (dom.evmDiagnosticBanner) dom.evmDiagnosticBanner.style.display = 'none';

    try {
      const res = await fetch('/api/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: cleanAddr })
      });
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || 'Inspection request failed');
      }

      // Case 1: EVM Cross-Chain Address Detected
      if (data.status === 'evm_diagnostic') {
        if (dom.evmNetworkTag) dom.evmNetworkTag.textContent = data.detected_network;
        if (dom.evmExplanationText) dom.evmExplanationText.textContent = `${data.message} ${data.explanation}`;
        if (dom.evmContractCode) dom.evmContractCode.textContent = data.address;
        if (dom.evmAssetName) dom.evmAssetName.textContent = data.identified_asset;
        if (dom.evmDiagnosticBanner) {
          dom.evmDiagnosticBanner.style.display = 'block';
          dom.evmDiagnosticBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        return;
      }

      // Case 2: Solana Token Full ML & Forensic Result
      if (data.status === 'success') {
        if (dom.evmDiagnosticBanner) dom.evmDiagnosticBanner.style.display = 'none';

        // Update local maps
        state.tokens.set(data.mint, data.token);
        state.audits.set(data.mint, data.forensics);
        state.activeMint = data.mint;

        // Render in Inspector with full ML cards
        renderInspector(data.token, data.forensics, data.classifier, data.survival, data.features);

        // Refresh token list
        renderTokenList();
      }
    } catch (err) {
      alert('Inspection Error: ' + err.message);
    } finally {
      if (dom.btnInspectCoin) {
        dom.btnInspectCoin.disabled = false;
        dom.btnInspectCoin.textContent = '⚡ Audit & Predict';
      }
    }
  }

  // =========================================================================
  // Cohort Audit & Active Learning Controller
  // =========================================================================
  let activeCohortBucket = 'rugs';

  function switchViewMode(mode) {
    if (mode === 'stream') {
      if (dom.btnViewLiveStream) dom.btnViewLiveStream.classList.add('active');
      if (dom.btnViewCohorts) dom.btnViewCohorts.classList.remove('active');
      if (dom.btnViewPaperTrading) dom.btnViewPaperTrading.classList.remove('active');
      if (dom.streamDashboardView) dom.streamDashboardView.style.display = 'grid';
      if (dom.cohortDashboardView) dom.cohortDashboardView.style.display = 'none';
      if (dom.paperTradingView) dom.paperTradingView.style.display = 'none';
    } else if (mode === 'cohorts') {
      if (dom.btnViewLiveStream) dom.btnViewLiveStream.classList.remove('active');
      if (dom.btnViewCohorts) dom.btnViewCohorts.classList.add('active');
      if (dom.btnViewPaperTrading) dom.btnViewPaperTrading.classList.remove('active');
      if (dom.streamDashboardView) dom.streamDashboardView.style.display = 'none';
      if (dom.cohortDashboardView) dom.cohortDashboardView.style.display = 'flex';
      if (dom.paperTradingView) dom.paperTradingView.style.display = 'none';
      fetchCohortSummary();
      switchCohortBucketTab(activeCohortBucket);
    } else if (mode === 'papertrading') {
      if (dom.btnViewLiveStream) dom.btnViewLiveStream.classList.remove('active');
      if (dom.btnViewCohorts) dom.btnViewCohorts.classList.remove('active');
      if (dom.btnViewPaperTrading) dom.btnViewPaperTrading.classList.add('active');
      if (dom.streamDashboardView) dom.streamDashboardView.style.display = 'none';
      if (dom.cohortDashboardView) dom.cohortDashboardView.style.display = 'none';
      if (dom.paperTradingView) dom.paperTradingView.style.display = 'flex';
      loadPaperTradingData();
    }
  }
  window.switchViewMode = switchViewMode;

  async function loadPaperTradingData() {
    try {
      const [sumRes, tradesRes] = await Promise.all([
        fetch('/api/paper-trading/summary'),
        fetch('/api/paper-trading/trades?limit=60')
      ]);
      const summary = await sumRes.json();
      const tradesData = await tradesRes.json();

      const scalp = summary.strategies?.SEAL_SCALPER || {};
      const freeroll = summary.strategies?.OLDWHALE_FREEROLLER || {};

      // Scalper Metrics
      if (dom.scalpPortfolioVal) dom.scalpPortfolioVal.textContent = `${(scalp.portfolio_value_sol || 10).toFixed(2)} SOL`;
      if (dom.scalpCashVal) dom.scalpCashVal.textContent = `${(scalp.cash_sol || 10).toFixed(2)} SOL`;
      if (dom.scalpRealizedPnl) {
        const pnl = scalp.realized_pnl_sol || 0;
        dom.scalpRealizedPnl.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(4)} SOL`;
        dom.scalpRealizedPnl.className = `s-val ${pnl >= 0 ? 'profit' : 'loss'}`;
      }
      if (dom.scalpReturnPct) {
        const ret = scalp.total_return_pct || 0;
        dom.scalpReturnPct.textContent = `${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%`;
        dom.scalpReturnPct.className = `s-val ${ret >= 0 ? 'profit' : 'loss'}`;
      }
      if (dom.scalpWinRate) dom.scalpWinRate.textContent = `${(scalp.win_rate_pct || 0).toFixed(1)}%`;
      if (dom.scalpOpenCount) dom.scalpOpenCount.textContent = scalp.active_positions_count || 0;

      // OldWhale Metrics
      if (dom.freerollPortfolioVal) dom.freerollPortfolioVal.textContent = `${(freeroll.portfolio_value_sol || 10).toFixed(2)} SOL`;
      if (dom.freerollCashVal) dom.freerollCashVal.textContent = `${(freeroll.cash_sol || 10).toFixed(2)} SOL`;
      if (dom.freerollRealizedPnl) {
        const pnl = freeroll.realized_pnl_sol || 0;
        dom.freerollRealizedPnl.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(4)} SOL`;
        dom.freerollRealizedPnl.className = `s-val ${pnl >= 0 ? 'profit' : 'loss'}`;
      }
      if (dom.freerollReturnPct) {
        const ret = freeroll.total_return_pct || 0;
        dom.freerollReturnPct.textContent = `${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%`;
        dom.freerollReturnPct.className = `s-val ${ret >= 0 ? 'profit' : 'loss'}`;
      }
      if (dom.freerollMoonbags) dom.freerollMoonbags.textContent = freeroll.moonbags_retained || 0;
      if (dom.freerollOpenCount) dom.freerollOpenCount.textContent = freeroll.active_positions_count || 0;

      // Totals
      if (dom.paperTotalPortfolio) dom.paperTotalPortfolio.textContent = `${(summary.total_portfolio_sol || 20).toFixed(2)} SOL`;
      if (dom.paperTotalRealizedPnl) {
        const totPnl = summary.total_realized_pnl_sol || 0;
        dom.paperTotalRealizedPnl.textContent = `${totPnl >= 0 ? '+' : ''}${totPnl.toFixed(4)} SOL`;
        dom.paperTotalRealizedPnl.className = totPnl >= 0 ? 'profit' : 'loss';
      }
      const activeCount = summary.active_positions_count || 0;
      if (dom.activePositionsCountBadge) dom.activePositionsCountBadge.textContent = `${activeCount} Open`;
      if (dom.paperActiveBadge) dom.paperActiveBadge.textContent = activeCount;

      // Render Active Positions Table
      const allPositions = [
        ...(scalp.active_positions || []),
        ...(freeroll.active_positions || [])
      ];
      if (dom.paperPositionsTableBody) {
        if (allPositions.length === 0) {
          dom.paperPositionsTableBody.innerHTML = '<tr><td colspan="9" class="td-empty">No active paper trading positions. Awaiting clean momentum signals...</td></tr>';
        } else {
          dom.paperPositionsTableBody.innerHTML = allPositions.map(pos => {
            const isScalp = pos.strategy === 'SEAL_SCALPER';
            const pnl = pos.unrealized_pnl_pct != null ? pos.unrealized_pnl_pct : 0;
            const pnlClass = pnl >= 0 ? 'profit' : 'loss';
            const targetText = isScalp 
              ? (pos.tp1_executed ? 'TP2 at +80%' : 'TP1 at +30%') 
              : (pos.freeroll_executed ? '🚀 Risk-Free Moonbag' : 'Break-Even at 2x (+100%)');
            const initialSol = (pos.initial_sol_invested != null ? pos.initial_sol_invested : 0);
            const marketValSol = (pos.current_market_value_sol != null ? pos.current_market_value_sol : (pos.remaining_tokens * (pos.current_price_sol || 0)) || 0);
            return `
              <tr>
                <td><span class="strat-badge ${isScalp ? 'scalp' : 'freeroll'}">${isScalp ? 'SEAL SCALP' : 'OLDWHALE'}</span></td>
                <td><strong>${escapeHtml(pos.symbol || 'UNKNOWN')}</strong> <span style="font-size: 11px; color: var(--text-dim);">(${escapeHtml(pos.name || '')})</span></td>
                <td><code class="val-mono">${escapeHtml((pos.mint || '').slice(0, 4))}...${escapeHtml((pos.mint || '').slice(-4))}</code></td>
                <td class="val-mono">${((pos.entry_price_sol || 0) * 1e9).toFixed(1)} lam</td>
                <td class="val-mono">${((pos.current_price_sol || 0) * 1e9).toFixed(1)} lam</td>
                <td><strong class="${pnlClass}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%</strong></td>
                <td class="val-mono">${initialSol.toFixed(2)} SOL</td>
                <td class="val-mono">${marketValSol.toFixed(4)} SOL</td>
                <td><span class="badge ${pos.freeroll_executed ? 'badge-accent' : 'badge-secondary'}">${targetText}</span></td>
              </tr>
            `;
          }).join('');
        }
      }

      // Render Trades Execution Log Table
      const trades = tradesData.trades || [];
      if (dom.totalTradesBadge) dom.totalTradesBadge.textContent = `${trades.length} Executions`;
      if (dom.paperTradesTableBody) {
        if (trades.length === 0) {
          dom.paperTradesTableBody.innerHTML = '<tr><td colspan="8" class="td-empty">No trades executed yet. Ingestion pipeline is scanning live bonding curve events...</td></tr>';
        } else {
          dom.paperTradesTableBody.innerHTML = trades.map(t => {
            const isScalp = t.strategy === 'SEAL_SCALPER';
            let actionTagClass = 'buy';
            let reasonBoxClass = 'tp';
            if (t.action.includes('TP')) { actionTagClass = 'tp1'; reasonBoxClass = 'tp'; }
            else if (t.action.includes('FREE_ROLL')) { actionTagClass = 'free-roll'; reasonBoxClass = 'freeroll'; }
            else if (t.action.includes('STOP')) { actionTagClass = 'stop'; reasonBoxClass = 'stop'; }
            else if (t.action.includes('EMERGENCY')) { actionTagClass = 'emergency'; reasonBoxClass = 'emergency'; }

            const pnl = t.realized_pnl_sol || 0;
            const pnlClass = pnl > 0 ? 'profit' : (pnl < 0 ? 'loss' : '');
            return `
              <tr>
                <td style="font-size: 11px; color: var(--text-dim);">${escapeHtml(t.timestamp)}</td>
                <td><span class="strat-badge ${isScalp ? 'scalp' : 'freeroll'}">${isScalp ? 'SEAL SCALP' : 'OLDWHALE'}</span></td>
                <td><span class="action-tag ${actionTagClass}">${escapeHtml(t.action)}</span></td>
                <td><strong>${escapeHtml(t.symbol)}</strong> <code class="val-mono" style="font-size: 10px;">(${escapeHtml(t.mint.slice(0, 4))}...${escapeHtml(t.mint.slice(-4))})</code></td>
                <td class="val-mono">${(t.sol_amount || 0).toFixed(4)} SOL</td>
                <td><strong class="${pnlClass}">${pnl !== 0 ? (pnl > 0 ? '+' : '') + pnl.toFixed(4) + ' SOL' : '—'}</strong></td>
                <td><strong class="${pnlClass}">${t.pnl_pct !== 0 ? (t.pnl_pct > 0 ? '+' : '') + t.pnl_pct.toFixed(1) + '%' : '—'}</strong></td>
                <td><div class="td-reason-box ${reasonBoxClass}">${escapeHtml(t.reason)}</div></td>
              </tr>
            `;
          }).join('');
        }
      }

    } catch (err) {
      console.warn('Error loading paper trading data:', err);
    }
  }
  window.loadPaperTradingData = loadPaperTradingData;

  async function resetPaperTrading() {
    if (!confirm('Are you sure you want to reset paper trading portfolios to 10.0 SOL? All active positions and history will be cleared.')) {
      return;
    }
    try {
      const res = await fetch('/api/paper-trading/reset', { method: 'POST' });
      const data = await res.json();
      alert(data.message || 'Portfolios reset successfully.');
      loadPaperTradingData();
    } catch (err) {
      alert('Error resetting paper trading: ' + err);
    }
  }
  window.resetPaperTrading = resetPaperTrading;


  async function fetchCohortSummary() {
    try {
      const res = await fetch('/api/cohorts/summary');
      const data = await res.json();

      if (dom.statCohortTotal) dom.statCohortTotal.textContent = data.total_audits || 0;
      if (dom.statCohortRugs) dom.statCohortRugs.textContent = data.confirmed_rugs || 0;
      if (dom.statCohortSurvivors) dom.statCohortSurvivors.textContent = data.surviving_candidates || 0;
      if (dom.statCohortReaudit) dom.statCohortReaudit.textContent = data.pending_t2_reaudit || 0;
      if (dom.statCohortGraduated) dom.statCohortGraduated.textContent = data.graduated_runners || 0;
      if (dom.statCohortCTO) dom.statCohortCTO.textContent = data.cto_takeovers || 0;
      if (dom.cohortPendingBadge) dom.cohortPendingBadge.textContent = data.pending_t1_audit || 0;

      if (dom.countTabRugs) dom.countTabRugs.textContent = data.confirmed_rugs || 0;
      if (dom.countTabSurvivors) dom.countTabSurvivors.textContent = data.surviving_candidates || 0;
      if (dom.countTabReaudit) dom.countTabReaudit.textContent = data.pending_t2_reaudit || 0;

      const lm = data.learning_metrics || {};
      if (dom.statCohortPrecision) {
        dom.statCohortPrecision.textContent = `${lm.precision_pct || 0}%`;
      }
    } catch (e) {
      console.debug('Cohort summary fetch failed:', e);
    }
  }

  function switchCohortBucketTab(bucket) {
    activeCohortBucket = bucket;
    [dom.paneRugs, dom.paneSurvivors, dom.paneReaudit, dom.paneLearning].forEach(p => {
      if (p) p.style.display = 'none';
    });

    if (bucket === 'rugs') {
      dom.paneRugs.style.display = 'flex';
      loadBucketRugs();
    } else if (bucket === 'survivors') {
      dom.paneSurvivors.style.display = 'flex';
      loadBucketSurvivors();
    } else if (bucket === 'reaudit') {
      dom.paneReaudit.style.display = 'flex';
      loadBucketReaudit();
    } else if (bucket === 'learning') {
      dom.paneLearning.style.display = 'flex';
      loadLearningMetrics();
    }
  }
  window.switchCohortBucketTab = switchCohortBucketTab;

  async function loadBucketRugs() {
    if (!dom.rugsTableBody) return;
    dom.rugsTableBody.innerHTML = '<tr><td colspan="9" class="td-loading">Fetching Rug Graveyard via DuckDB...</td></tr>';
    try {
      const res = await fetch('/api/cohorts/bucket/rugs?limit=50');
      const data = await res.json();
      const tokens = data.tokens || [];

      if (tokens.length === 0) {
        dom.rugsTableBody.innerHTML = '<tr><td colspan="9" class="td-empty">No tokens in Rug Graveyard yet. Click "Run Batch Audit" to classify older coins!</td></tr>';
        return;
      }

      dom.rugsTableBody.innerHTML = tokens.map(t => {
        const pChange = (t.price_change_24h || 0);
        const changeClass = pChange >= 0 ? 'val-pos' : 'val-neg';
        const tierClass = (t.initial_risk_tier || 'LOW').toLowerCase();
        const mcapStr = t.current_mcap_usd > 0 ? `$${Math.round(t.current_mcap_usd).toLocaleString()}` : '$0';
        const volStr = t.volume_24h > 0 ? `$${Math.round(t.volume_24h).toLocaleString()}` : '$0';
        const priceStr = t.current_price_usd > 0 ? `$${t.current_price_usd.toFixed(8)}` : '$0.00';

        return `
          <tr>
            <td>
              <strong>${escapeHtml(t.name || 'Unknown')}</strong>
              <div class="sub-dim">${escapeHtml(t.symbol || 'SOL')}</div>
            </td>
            <td>
              <code class="val-mono" title="${t.mint}">${t.mint.slice(0, 6)}...${t.mint.slice(-4)}</code>
            </td>
            <td>
              <span class="badge-tag ${tierClass}">${t.initial_risk_tier || 'LOW'} (${t.initial_risk_score || 0})</span>
            </td>
            <td style="max-width: 260px;">
              <small class="dim-text">${escapeHtml(t.audit_notes || 'Confirmed collapse')}</small>
            </td>
            <td class="val-mono">${priceStr}</td>
            <td class="val-mono font-bold">${mcapStr}</td>
            <td class="val-mono">${volStr}</td>
            <td class="${changeClass} font-bold">${pChange >= 0 ? '+' : ''}${pChange.toFixed(1)}%</td>
            <td>
              <button class="btn-sm btn-secondary" onclick="auditSingleFromCohort('${t.mint}')">🔬 Audit</button>
            </td>
          </tr>
        `;
      }).join('');
    } catch (err) {
      dom.rugsTableBody.innerHTML = `<tr><td colspan="9" class="td-empty">Error loading rugs: ${err.message}</td></tr>`;
    }
  }

  async function loadBucketSurvivors() {
    if (!dom.survivorsTableBody) return;
    dom.survivorsTableBody.innerHTML = '<tr><td colspan="9" class="td-loading">Fetching Surviving Candidates...</td></tr>';
    try {
      const res = await fetch('/api/cohorts/bucket/survivors?limit=50');
      const data = await res.json();
      const tokens = data.tokens || [];

      if (tokens.length === 0) {
        dom.survivorsTableBody.innerHTML = '<tr><td colspan="9" class="td-empty">No surviving candidates found yet.</td></tr>';
        return;
      }

      dom.survivorsTableBody.innerHTML = tokens.map(t => {
        const pChange = (t.price_change_24h || 0);
        const changeClass = pChange >= 0 ? 'val-pos' : 'val-neg';
        const mcapStr = t.current_mcap_usd > 0 ? `$${Math.round(t.current_mcap_usd).toLocaleString()}` : '$0';
        const volStr = t.volume_24h > 0 ? `$${Math.round(t.volume_24h).toLocaleString()}` : '$0';
        const isGrad = t.is_graduated || t.status === 'GRADUATED';
        const statusBadge = t.status === 'CTO' 
          ? '<span class="badge-tag cto">🤝 CTO TAKEOVER</span>'
          : (isGrad ? '<span class="badge-tag graduated">🚀 GRADUATED</span>' : '<span class="badge-tag low">🛡️ ACTIVE</span>');

        return `
          <tr>
            <td>
              <strong>${escapeHtml(t.name || 'Unknown')}</strong>
              <div class="sub-dim">${escapeHtml(t.symbol || 'SOL')}</div>
            </td>
            <td>
              <code class="val-mono" title="${t.mint}">${t.mint.slice(0, 6)}...${t.mint.slice(-4)}</code>
            </td>
            <td>${statusBadge}</td>
            <td class="val-mono font-bold">${mcapStr}</td>
            <td class="val-mono">${volStr}</td>
            <td class="${changeClass} font-bold">${pChange >= 0 ? '+' : ''}${pChange.toFixed(1)}%</td>
            <td>${isGrad ? '✅ Migrated' : '⏳ Bonding Curve'}</td>
            <td style="max-width: 240px;">
              <small class="dim-text">${escapeHtml(t.audit_notes || 'Maintains liquidity floor')}</small>
            </td>
            <td>
              <button class="btn-sm btn-primary" onclick="auditSingleFromCohort('${t.mint}')">🔬 Inspect</button>
            </td>
          </tr>
        `;
      }).join('');
    } catch (err) {
      dom.survivorsTableBody.innerHTML = `<tr><td colspan="9" class="td-empty">Error loading survivors: ${err.message}</td></tr>`;
    }
  }

  async function loadBucketReaudit() {
    if (!dom.reauditCardsContainer) return;
    dom.reauditCardsContainer.innerHTML = '<div class="loading-state"><div class="spinner"></div><span>Loading tokens for long-term re-audit...</span></div>';
    try {
      const res = await fetch('/api/cohorts/bucket/reaudit?limit=30');
      const data = await res.json();
      const tokens = data.tokens || [];

      if (tokens.length === 0) {
        dom.reauditCardsContainer.innerHTML = `
          <div class="inspector-placeholder" style="grid-column: 1 / -1; padding: 40px 20px;">
            <div class="placeholder-icon">🎉</div>
            <h3>All Multi-Day Cohorts Reviewed!</h3>
            <p>No tokens currently pending human re-audit. As tokens age past 3 days, they will automatically populate here for ground-truth verification.</p>
          </div>
        `;
        return;
      }

      dom.reauditCardsContainer.innerHTML = tokens.map(t => {
        const mcapStr = t.current_mcap_usd > 0 ? `$${Math.round(t.current_mcap_usd).toLocaleString()}` : '$0';
        const volStr = t.volume_24h > 0 ? `$${Math.round(t.volume_24h).toLocaleString()}` : '$0';

        return `
          <div class="reaudit-card" id="cardReaudit_${t.mint}">
            <div class="reaudit-card-header">
              <div class="reaudit-title-group">
                <h4>${escapeHtml(t.name || 'Token')} (${escapeHtml(t.symbol || 'SOL')})</h4>
                <code class="val-mono">${t.mint.slice(0, 8)}...${t.mint.slice(-6)}</code>
              </div>
              <span class="badge-tag ${t.initial_risk_tier.toLowerCase()}">Initial: ${t.initial_risk_tier}</span>
            </div>

            <div class="reaudit-metrics-grid">
              <div class="reaudit-metric-item">
                <span class="lbl">Mcap</span>
                <span class="val">${mcapStr}</span>
              </div>
              <div class="reaudit-metric-item">
                <span class="lbl">24h Vol</span>
                <span class="val">${volStr}</span>
              </div>
              <div class="reaudit-metric-item">
                <span class="lbl">Stage 1 Note</span>
                <span class="val" style="font-size: 10px; font-weight: normal;">${escapeHtml((t.audit_notes || '').slice(0, 28))}...</span>
              </div>
            </div>

            <div class="reaudit-actions-strip">
              <span class="lbl" style="font-size: 10px; color: var(--text-dim); text-transform: uppercase;">Submit Ground-Truth Re-Audit Verdict:</span>
              <div class="reaudit-btn-group">
                <button class="btn-verdict rug" onclick="submitReauditVerdict('${t.mint}', 'SLOW_RUG')">💀 Confirm Slow Rug</button>
                <button class="btn-verdict grad" onclick="submitReauditVerdict('${t.mint}', 'GRADUATED')">🚀 Graduated / Mooner</button>
                <button class="btn-verdict cto" onclick="submitReauditVerdict('${t.mint}', 'CTO')">🤝 Mark CTO</button>
              </div>
              <textarea class="reaudit-notes-input" id="notes_${t.mint}" rows="2" placeholder="Optional learning notes (e.g. dev split wallets, community took over Telegram...)"></textarea>
            </div>
          </div>
        `;
      }).join('');
    } catch (err) {
      dom.reauditCardsContainer.innerHTML = `<div class="error-msg">Error: ${err.message}</div>`;
    }
  }

  let activeMatrixQuadrant = 'tp';
  let cachedLearningData = null;

  async function loadLearningMetrics() {
    try {
      const res = await fetch('/api/cohorts/learning-metrics');
      const data = await res.json();
      cachedLearningData = data;
      const lm = data.learning_metrics || {};

      if (dom.matrixTP) dom.matrixTP.textContent = (lm.true_positives || 0).toLocaleString();
      if (dom.matrixFP) dom.matrixFP.textContent = (lm.false_positives_cto || 0).toLocaleString();
      if (dom.matrixFN) dom.matrixFN.textContent = (lm.false_negatives_missed || 0).toLocaleString();
      if (dom.matrixTN) dom.matrixTN.textContent = (lm.true_negatives || 0).toLocaleString();

      if (dom.countMatrixTabTP) dom.countMatrixTabTP.textContent = (lm.true_positives || 0).toLocaleString();
      if (dom.countMatrixTabFP) dom.countMatrixTabFP.textContent = (lm.false_positives_cto || 0).toLocaleString();
      if (dom.countMatrixTabFN) dom.countMatrixTabFN.textContent = (lm.false_negatives_missed || 0).toLocaleString();
      if (dom.countMatrixTabTN) dom.countMatrixTabTN.textContent = (lm.true_negatives || 0).toLocaleString();

      if (dom.lblAccuracy) dom.lblAccuracy.textContent = `${lm.accuracy_pct || 0}%`;
      if (dom.lblPrecision) dom.lblPrecision.textContent = `${lm.precision_pct || 0}%`;
      if (dom.lblRecall) dom.lblRecall.textContent = `${lm.recall_pct || 0}%`;

      // Render Rule Attribution Table
      if (dom.ruleAttributionBody) {
        const rules = data.rule_attribution || [];
        if (rules.length === 0) {
          dom.ruleAttributionBody.innerHTML = '<tr><td colspan="5" class="td-empty">No rule attribution data yet. Run batch audits to benchmark forensic flags!</td></tr>';
        } else {
          dom.ruleAttributionBody.innerHTML = rules.map(r => `
            <tr>
              <td><strong>${escapeHtml(r.flag)}</strong></td>
              <td class="val-mono">${r.total_triggers}</td>
              <td class="val-mono val-neg">${r.actual_rugs}</td>
              <td class="val-mono val-pos">${r.false_alarms}</td>
              <td class="val-mono font-bold" style="color: ${r.precision_pct >= 85 ? '#10b981' : '#f59e0b'};">
                ${r.precision_pct}%
              </td>
            </tr>
          `).join('');
        }
      }

      // Render the currently active matrix quadrant table
      filterMatrixQuadrant(activeMatrixQuadrant, false);

    } catch (e) {
      console.debug('Failed to load learning metrics:', e);
    }
  }

  async function filterMatrixQuadrant(quadrant = 'tp', fetchFresh = true) {
    activeMatrixQuadrant = quadrant.toLowerCase();

    // 1. Highlight active matrix cell
    const cells = [dom.cellMatrixTP, dom.cellMatrixFP, dom.cellMatrixFN, dom.cellMatrixTN];
    cells.forEach(c => { if (c) c.classList.remove('active-selected'); });
    if (activeMatrixQuadrant === 'tp' && dom.cellMatrixTP) dom.cellMatrixTP.classList.add('active-selected');
    if (activeMatrixQuadrant === 'fp' && dom.cellMatrixFP) dom.cellMatrixFP.classList.add('active-selected');
    if (activeMatrixQuadrant === 'fn' && dom.cellMatrixFN) dom.cellMatrixFN.classList.add('active-selected');
    if (activeMatrixQuadrant === 'tn' && dom.cellMatrixTN) dom.cellMatrixTN.classList.add('active-selected');

    // 2. Highlight active tab button
    const tabs = [dom.tabMatrixTP, dom.tabMatrixFP, dom.tabMatrixFN, dom.tabMatrixTN];
    tabs.forEach(t => { if (t) t.classList.remove('active'); });
    if (activeMatrixQuadrant === 'tp' && dom.tabMatrixTP) dom.tabMatrixTP.classList.add('active');
    if (activeMatrixQuadrant === 'fp' && dom.tabMatrixFP) dom.tabMatrixFP.classList.add('active');
    if (activeMatrixQuadrant === 'fn' && dom.tabMatrixFN) dom.tabMatrixFN.classList.add('active');
    if (activeMatrixQuadrant === 'tn' && dom.tabMatrixTN) dom.tabMatrixTN.classList.add('active');

    // 3. Update Title & Hint
    if (dom.matrixExplorerTitle) {
      const titles = {
        'tp': '🎯 Confirmed True Positives (Predicted Rug & Accurately Caught)',
        'fp': '🤝 False Positives / CTOs (Predicted Rug, But Survived)',
        'fn': '⚠️ False Negatives (Predicted Clean, But Slow-Rugged)',
        'tn': '🛡️ True Negatives (Predicted Clean & Confirmed Survived)'
      };
      dom.matrixExplorerTitle.textContent = titles[activeMatrixQuadrant] || 'Confusion Matrix Coins';
    }
    if (dom.matrixExplorerHint) {
      const hints = {
        'tp': 'Tokens accurately caught by the forensic & Bayesian engine at launch that subsequent 10h audits confirmed dead.',
        'fp': 'Tokens flagged high-risk at Block 0 that survived anyway (e.g. community takeovers or dev dump bought up).',
        'fn': 'Tokens initially deemed low-risk that subsequently dumped or drained liquidity (missed soft-rugs).',
        'tn': 'Tokens deemed low-risk that successfully survived, established organic liquidity, and held price floors.'
      };
      dom.matrixExplorerHint.textContent = hints[activeMatrixQuadrant] || '';
    }

    // 4. Fetch coins for this quadrant
    if (!dom.matrixCohortTableBody) return;
    dom.matrixCohortTableBody.innerHTML = '<tr><td colspan="7" class="td-loading">Loading quadrant tokens...</td></tr>';

    try {
      const res = await fetch(`/api/cohorts/matrix-tokens?type=${activeMatrixQuadrant}&limit=50`);
      const data = await res.json();
      const tokens = data.tokens || [];

      if (tokens.length === 0) {
        dom.matrixCohortTableBody.innerHTML = `<tr><td colspan="7" class="td-empty">No tokens in the ${activeMatrixQuadrant.toUpperCase()} quadrant.</td></tr>`;
        return;
      }

      dom.matrixCohortTableBody.innerHTML = tokens.map(t => {
        const tier = t.initial_risk_tier || 'LOW';
        let tierBadgeClass = 'badge-low';
        if (tier === 'CRITICAL') tierBadgeClass = 'badge-danger';
        else if (tier === 'HIGH') tierBadgeClass = 'badge-warning';
        else if (tier === 'MEDIUM') tierBadgeClass = 'badge-secondary';

        const status = t.status || 'NEW';
        let statusBadgeClass = 'badge-secondary';
        if (status === 'CONFIRMED_RUG' || status === 'SLOW_RUG') statusBadgeClass = 'badge-danger';
        else if (status === 'SURVIVING_CANDIDATE') statusBadgeClass = 'badge-success';
        else if (status === 'CTO') statusBadgeClass = 'badge-accent';
        else if (status === 'GRADUATED') statusBadgeClass = 'badge-primary';

        const mcap = Math.round(t.current_mcap_usd || 0);
        const vol = Math.round(t.volume_24h || 0);
        const score = t.initial_risk_score != null ? t.initial_risk_score : 50;

        return `
          <tr>
            <td>
              <strong>${escapeHtml(t.symbol || 'PUMP')}</strong> 
              <span style="font-size: 11px; color: var(--text-dim);">(${escapeHtml(t.name || '')})</span>
            </td>
            <td>
              <a href="https://pump.fun/coin/${t.mint}" target="_blank" class="val-mono" style="color: #60a5fa; text-decoration: none;" title="View on Pump.fun">
                ${t.mint.slice(0, 4)}...${t.mint.slice(-4)} ↗
              </a>
            </td>
            <td>
              <span class="badge ${tierBadgeClass}">${score} ${tier}</span>
            </td>
            <td>
              <span class="badge ${statusBadgeClass}">${escapeHtml(status)}</span>
            </td>
            <td class="val-mono font-bold">$${mcap.toLocaleString()}</td>
            <td class="val-mono">$${vol.toLocaleString()}</td>
            <td><small style="color: var(--text-secondary);">${escapeHtml(t.audit_notes || t.human_notes || '—')}</small></td>
          </tr>
        `;
      }).join('');

    } catch (err) {
      dom.matrixCohortTableBody.innerHTML = `<tr><td colspan="7" class="td-error">Failed to load quadrant tokens: ${escapeHtml(err.message)}</td></tr>`;
    }
  }
  window.filterMatrixQuadrant = filterMatrixQuadrant;

  async function runCohortBatchAudit() {
    if (dom.btnRunCohortBatch) {
      dom.btnRunCohortBatch.disabled = true;
      dom.btnRunCohortBatch.innerHTML = '<span>⏳</span> Auditing T+10h Coins...';
    }
    try {
      const res = await fetch('/api/cohorts/audit-batch?hours_threshold=10.0&limit=60', { method: 'POST' });
      const data = await res.json();
      alert(`Batch Audit Complete!\n\nProcessed: ${data.processed || 0} tokens\n💀 New Rugs: ${data.new_rugs || 0}\n🛡️ New Survivors: ${data.new_survivors || 0}`);
      fetchCohortSummary();
      switchCohortBucketTab(activeCohortBucket);
    } catch (err) {
      alert('Batch audit error: ' + err.message);
    } finally {
      if (dom.btnRunCohortBatch) {
        dom.btnRunCohortBatch.disabled = false;
        dom.btnRunCohortBatch.innerHTML = '<span>⚡</span> Run Batch Audit (T+10h)';
      }
    }
  }

  async function reloadCohortPrices(bucket = 'rugs') {
    const btn = bucket === 'rugs' ? dom.btnReloadRugPrices : dom.btnReloadSurvivorPrices;
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span>⏳</span> Reloading DexScreener Prices...';
    }
    try {
      const res = await fetch('/api/cohorts/refresh-prices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bucket, limit: 60 })
      });
      const data = await res.json();

      if (data.revived_count > 0 && dom.rugsAlertBanner) {
        dom.rugsAlertBanner.style.display = 'block';
        dom.rugsAlertBanner.innerHTML = `
          ⚡ <strong>${data.revived_count} REVIVED CTO TOKEN(S) DETECTED!</strong><br>
          ${data.revived_tokens.map(r => `• <strong>${escapeHtml(r.symbol || 'Token')}</strong>: Mcap surged to $${Math.round(r.mcap).toLocaleString()} ($${Math.round(r.volume_24h).toLocaleString()} 24h vol)`).join('<br>')}
        `;
      }

      fetchCohortSummary();
      switchCohortBucketTab(activeCohortBucket);
    } catch (err) {
      alert('Price reload error: ' + err.message);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<span>🔄</span> Reload ${bucket === 'rugs' ? 'Rug' : 'Survivor'} Prices`;
      }
    }
  }

  // Global helper functions attached to window for table/card inline clicks
  window.auditSingleFromCohort = function(mint) {
    switchViewMode('stream');
    if (dom.inspectCoinInput) dom.inspectCoinInput.value = mint;
    inspectAddress(mint);
  };

  window.submitReauditVerdict = async function(mint, verdict) {
    const notesElem = document.getElementById(`notes_${mint}`);
    const notes = notesElem ? notesElem.value.trim() : '';

    try {
      const res = await fetch(`/api/cohorts/human-audit/${mint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ verdict, notes })
      });
      const data = await res.json();

      if (data.status === 'success') {
        const card = document.getElementById(`cardReaudit_${mint}`);
        if (card) {
          card.style.opacity = '0.5';
          card.innerHTML = `<div style="text-align: center; padding: 20px; color: #10b981;">✅ Recorded Verdict: <strong>${verdict}</strong>. Model updated!</div>`;
          setTimeout(() => card.remove(), 1800);
        }
        fetchCohortSummary();
      }
    } catch (e) {
      alert('Failed to save verdict: ' + e.message);
    }
  };

  // Run app on DOM ready
  document.addEventListener('DOMContentLoaded', () => {
    init();
    fetchCohortSummary();
  });

})();

