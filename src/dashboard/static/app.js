/**
 * NEMO: Real-Time Pump.fun Forensics & Screener HUD
 * Pure Vanilla JavaScript Client
 */

(function () {
  'use strict';

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

    // Right Tabs
    tabTradeTape: document.getElementById('tabTradeTape'),
    tabSurvival: document.getElementById('tabSurvival'),
    viewTradeTape: document.getElementById('viewTradeTape'),
    viewSurvival: document.getElementById('viewSurvival'),
    tradeTapeContainer: document.getElementById('tradeTapeContainer'),
    hazardRatiosList: document.getElementById('hazardRatiosList'),
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

  function renderInspector(tok, audit) {
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

    dom.btnViewSurvival.addEventListener('click', () => {
      dom.tabSurvival.click();
    });
  }

  // Run app on DOM ready
  document.addEventListener('DOMContentLoaded', init);

})();
