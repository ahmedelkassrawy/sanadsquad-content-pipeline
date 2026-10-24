# Marketing Research Sources (Draft)

> [!NOTE]
> This document describes the draft sources configuration defined in `inputs/sources.json`.
> Domain lists represent an initial reputable baseline and remain subject to calibration based on search retrieval performance.

## Overview

The marketing research agent draws exclusively from allowlisted domains specified per service line in `inputs/sources.json`. A global `_deny` list prevents indexing or citation of content farms, low-quality aggregators, and paywalled spam.

Sources within each service line are categorized into five groups:
- **`blogs`**: Research writeups, vulnerability disclosures, and technical engineering blogs.
- **`docs`**: Official documentation, technical standards, and framework specifications.
- **`production`**: Real-world deployment artifacts, release announcements, changelogs, and registry updates.
- **`analysis`**: Quantitative metrics, breach post-mortems, financial figures, and adoption curves.
- **`figures`**: Domain specialists and security researchers whose public commentary reflects current technical discourse.

---

## Service Line Group Roles

### 1. Web3 Security (`web3-security`)
- **`blogs`**: Smart contract security disclosures, auditor research, and vulnerability deep-dives (e.g., Trail of Bits, OpenZeppelin, Zellic).
- **`docs`**: Core language, compiler, and framework documentation for EVM and Solana/SVM (e.g., Solidity, Solana Docs, Foundry, Anchor).
- **`production`**: Standards trackers, block explorers, and protocol contract deployments (e.g., EIPs, Etherscan, Solscan).
- **`analysis`**: Exploit post-mortems, protocol hack losses, TVL trends, and ecosystem metrics (e.g., DeFiLlama, Rekt News, Dune, L2BEAT).
- **`figures`**: Prominent smart contract security researchers and protocol designers analyzing EVM/SVM vulnerabilities.

### 2. Web2 Pentesting (`pentesting`)
- **`blogs`**: Infrastructure, cloud security, API pentesting, and adversary tradecraft research (e.g., PortSwigger, Cloudflare, Unit 42).
- **`docs`**: Authoritative offensive security standards, vulnerability schemas, and defensive frameworks (e.g., OWASP, NIST, MITRE ATT&CK, CVE).
- **`production`**: Known vulnerability feeds, government alerts, and vendor security release trackers (e.g., CISA KEV, NVD, Google Security).
- **`analysis`**: Global cybersecurity threat intelligence, breach statistics, and incident reports (e.g., BleepingComputer, Mandiant, The Hacker News).
- **`figures`**: Established security practitioners and incident handlers offering real-time perspective on active exploits.

### 3. Agentic Security & Red Teaming (`agentic-security`)
- **`blogs`**: AI red teaming reports, prompt injection mechanics, and jailbreak research (e.g., Simon Willison, Embrace The Red, OpenAI, Anthropic).
- **`docs`**: LLM security taxonomies, agent risk management guidelines, and tool safety specifications (e.g., OWASP GenAI Top 10, NIST AI RMF, Anthropic Docs).
- **`production`**: Open-source agent runtime specifications, tool protocols, model cards, and security releases (e.g., Model Context Protocol, Hugging Face, LangChain, PyPI).
- **`analysis`**: Rigorous safety evaluations, model attack benchmarks, and agent capabilities tracking (e.g., arXiv, Epoch AI, Artificial Analysis).
- **`figures`**: AI safety specialists and machine learning red teamers dissecting prompt vulnerabilities and runtime tool boundaries.

### 4. Agentic Payment Protocols (`agentic-payments`)
- **`blogs`**: Autonomous commerce discussions, programmable wallet announcements, and payment rail updates (e.g., Coinbase, Stripe, Circle).
- **`docs`**: Agentic protocol specifications including x402 and MPP, programmable wallet APIs, and web payment standards (e.g., Coinbase CDP, Stripe Docs, Circle Dev, EIPs, W3C).
- **`production`**: Payment SDK packages, network payment rails, and autonomous transaction facilitators (e.g., Base, Solana, GitHub, npm).
- **`analysis`**: Transaction volume tracking, facilitator market share, and autonomous agent payment economics (e.g., Dune, DeFiLlama, Token Terminal, Visa).
- **`figures`**: Crypto-economic researchers and payment protocol architects analyzing autonomous machine payments and settlement models.

---

## Global Deny List (`_deny`)

Domains in `_deny` are rejected globally across all service lines. This filter blocks content farms, AI-generated search spam, low-reputation question hubs, and paywalled aggregators (e.g., Medium, Quora, GeeksforGeeks, Tutorialspoint, Forbes, Cointelegraph) to guarantee all marketing posts cite primary or authoritative sources.
