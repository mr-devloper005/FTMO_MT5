# Reading List — books and research papers

Compiled 2026-09-21 from the prior session that produced the FTMO work plan.
Citations below were checked against primary sources on 2026-09-21; see
**Verification** at the end for exactly what was and wasn't re-fetched.

## On the films

**No films were ever mentioned.** Neither this session nor the prior one
recommended a single movie or documentary. The earlier list had two parts —
*Books* and *Research papers* — and nothing else. If your manager is expecting
a film list, it needs to come from them, because there is nothing here to
recover. Ask which conversation they had in mind.

---

## Tier 1 — Your own codebase's source material

These three are the reason this list matters. `ftmo_mt5/ml/` already implements
what they describe, so you would be reading to understand code you have
running, not to learn something new and unattached.

| # | Source | Why | Est. |
|---|---|---|---|
| 1 | **Advances in Financial Machine Learning**, ch. 3, 4, 7 — Marcos López de Prado (Wiley, 2018), ISBN 978-1-119-48208-6 | Ch. 3 Labeling = your triple-barrier + meta-labeling. Ch. 4 Sample Weights = overlapping outcomes, uniqueness, time decay. Ch. 7 Cross-Validation in Finance = your `PurgedKFold`. | ~3 evenings |
| 2 | **The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality** — Bailey & López de Prado, *Journal of Portfolio Management* 40(5), 94–107 (2014). [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) | Explains directly why 17 winners out of 449 runs was not evidence. Implemented in your `validation.py`. Short. | 1 evening |
| 3 | **The Probability of Backtest Overfitting** — Bailey, Borwein, López de Prado & Zhu, *Journal of Computational Finance* 20(4), 39–69 (2017). [SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) | The CSCV method your `validation.py` implements. | 1 evening |

**If you read nothing else, read these three.** Done when you can explain,
unprompted, what breaks without purging.

## Tier 2 — Explicitly named in the prior session

### Books

| Source | Note | Est. |
|---|---|---|
| **Build a DeepSeek Model (From Scratch)** — Raj Dandekar, Rajat Dandekar, Sreedath Panat, Naman Dwivedi (Manning) | Listed as a best-seller on Vizuara's site. Note the prior session's own advice was *not* to buy the Vizuara course — from-scratch transformers are not your bottleneck. The book carries the same caveat. | ~15–20 h |

### Vizuara AI Labs papers — all four verified real

| # | Paper | Venue | Link |
|---|---|---|---|
| 4 | Simulating Misinformation Propagation in Social Networks using Large Language Models | **LASS workshop** at ACM CIKM 2025 (Outstanding Paper) — a workshop, not the main track | [arXiv:2511.10384](https://arxiv.org/abs/2511.10384) |
| 5 | EARS-UDE: Evaluating Auditory Response in Sensory Overload with Universal Differential Equations | NeurIPS AI4Science 2025 (Spotlight) | [arXiv:2510.26804](https://arxiv.org/abs/2510.26804) |
| 6 | Regional-TinyStories: A Small Language Model Framework for Evaluating Language Learning, Tokenizers, and Datasets | IJCNLP-AACL 2025 **Findings**, pp. 2318–2367 | [ACL Anthology](https://aclanthology.org/2025.findings-ijcnlp.142/) |
| 7 | Decoders Laugh as Loud as Encoders | arXiv preprint, Sept 2025 | [arXiv:2509.04779](https://arxiv.org/abs/2509.04779) |

## Tier 3 — Canonical background

Named as techniques in the prior session without citation. Nothing here maps to
running code you own except ADWIN.

### Drift detection — in your `drift_detection.py`

| # | Source | Note |
|---|---|---|
| 8 | **Learning from Time-Changing Data with Adaptive Windowing** — Bifet & Gavaldà, *SIAM SDM* 2007, pp. 443–448 | The ADWIN detector. |
| — | Population Stability Index | **No canonical paper exists.** Credit-risk industry practice. Don't hunt for an origin paper; read model-monitoring literature instead. |

### LLM / GenAI foundations

| # | Source |
|---|---|
| 9 | **Attention Is All You Need** — Vaswani et al., NeurIPS 2017 ([arXiv:1706.03762](https://arxiv.org/abs/1706.03762)) |
| 10 | **LoRA: Low-Rank Adaptation of Large Language Models** — Hu et al., 2021 ([arXiv:2106.09685](https://arxiv.org/abs/2106.09685)) |
| 11 | **QLoRA: Efficient Finetuning of Quantized LLMs** — Dettmers et al., 2023 ([arXiv:2305.14314](https://arxiv.org/abs/2305.14314)) |

### Scientific ML

| # | Source |
|---|---|
| 12 | **Physics-Informed Neural Networks** — Raissi, Perdikaris & Karniadakis, *J. Comp. Physics* 378 (2019) |
| 13 | **Neural Ordinary Differential Equations** — Chen et al., NeurIPS 2018 ([arXiv:1806.07366](https://arxiv.org/abs/1806.07366)) |
| 14 | **Universal Differential Equations for Scientific ML** — Rackauckas et al., 2020 ([arXiv:2001.04385](https://arxiv.org/abs/2001.04385)) — the method behind EARS-UDE (#5) |

### Vision and architecture

| # | Source |
|---|---|
| 15 | **An Image is Worth 16x16 Words** (ViT) — Dosovitskiy et al., ICLR 2021 ([arXiv:2010.11929](https://arxiv.org/abs/2010.11929)) |
| 16 | **Training Data-Efficient Image Transformers** (DeiT) — Touvron et al., 2021 ([arXiv:2012.12877](https://arxiv.org/abs/2012.12877)) |
| 17 | **A Path Towards Autonomous Machine Intelligence** — LeCun, 2022 — the JEPA position paper |

---

## Realistic scope

18 items: 2 books, 16 papers. Read properly — understood, not skimmed — this is
**roughly 50–60 hours**, and the DeepSeek book is a third of it. At an hour a
night that is two months.

Tier 1 alone is about **10 hours** and covers everything connected to code you
have actually written. That is the defensible subset if you need to report
progress before the whole list is done.

## Verification

Re-fetched from primary sources on 2026-09-21: items **1, 2, 3, 4, 5, 6, 7, 8**.
Two corrections to the original list came out of that check:

- Item 4 was at a **workshop** at CIKM 2025, not the main conference.
- Item 6 is in **Findings**, and its author list is longer than originally given.

Items **9–17** are standard, widely-cited works quoted from knowledge rather
than re-fetched. High confidence, but confirm the exact year and full author
list before putting any of them in something formal.
