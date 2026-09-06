# Nemo: Algorithmic Research & Anti-Fraud Engine for Pump.fun

Nemo is a quantitative research framework and forensic detection pipeline designed to investigate the market dynamics, survivability, and algorithmic soft-rug mechanics of tokens launched on the Pump.fun bonding curve.

---

## Documentation

The complete architectural blueprint, mathematical formulations, algorithmic models, and phased development roadmap are documented in:
* **[PUMP_FUN_RESEARCH_AND_DEVELOPMENT_SPEC.md](file:///Users/deepshah/Downloads/algorithmic-trading-python/nemo/docs/PUMP_FUN_RESEARCH_AND_DEVELOPMENT_SPEC.md)**

---

## Core Focus Areas

1. **On-Chain Forensics & Block-0 Sniping**: Detecting Jito MEV bundles and same-slot supply cornering.
2. **Graph-Theoretic Sybil Detection**: Directed Acyclic Graph (DAG) funding ancestry tracing and community detection across buyer clusters.
3. **Concentration & Wealth Inequality**: Real-time Herfindahl-Hirschman Index (HHI) and Gini coefficient tracking.
4. **Market Microstructure & Order Toxicity**: Volume-Synchronized Probability of Toxicity (VPIN) and Shannon entropy wash-trading analysis.
5. **Content Forensics**: Perceptual image hashing (pHash) against historical meme asset databases to identify cloned copycats.
6. **Machine Learning & Survival Analysis**: Cox Proportional Hazards regression and LightGBM classification modeling token hazard rates and lifespans.

---

## Phased Development Overview

| Phase | Module | Primary Objective |
| :--- | :--- | :--- |
| **Phase 1** | **Ingestion & Storage** | Stream real-time data via `pumpdev.io` WebSocket & log to DuckDB/SQLite; Solana RPC client. |
| **Phase 2** | **On-Chain Forensics** | Block-0 bundle analysis, Jito tip checking, Sybil funding clustering, HHI/Gini inequality. |
| **Phase 3** | **Microstructure & Content** | VPIN toxicity metric, trade entropy wash trading filter, pHash image clone detection. |
| **Phase 4** | **Modeling & Survival Analysis**| Historical cohort generation, Cox Proportional Hazards, LightGBM classifier with SHAP. |
| **Phase 5** | **Research Dashboard** | Real-time screener, sub-500ms pipeline, interactive analytics UI. |
