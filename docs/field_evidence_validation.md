# 字段绑定离线快照

生成时间：2026-10-13T11:37:06.711035+00:00

本报告读取现有抽取与索引，不重新抽取，不调用模型。候选引用可定位不等于事实得到支持；未进行独立人工/PDF复核。

支持状态：`{'unverified': 15}`；引用状态：`{'valid': 10, 'missing': 5}`。统计仅覆盖本次预算内展示的字段，不是准确率。

## A Lightweight Vision Transformer and Retinal Biomarker Fusion Framework for Early Glaucoma Detection: Toward Improved Clinical Screening.

论文：`europepmc:PMC13413154`；索引版本：`8c9c0a63aea6d72ae581a562c6c7413620bbae9f2e84b09875093f42dba917a7`。

- **research_goal**："Develop an automated framework for early detection and classification of glaucoma using structural changes in the optic nerve and retinal biomarkers."；`valid` / `unverified`。
  - 1. Introduction / 页 3 / `europepmc:PMC13413154::5`：《A Lightweight Vision Transformer and Retinal Biomarker Fusion Framework for Early Glaucoma Detection: Toward Improved Clinical Screening.》 1. Introduction J. Clin. Med. 2026, 15, 5651 3 of 26 into the OC and OD. Segmented images are used to extract the characteristics from the
  - Abstract / 页 1 / `europepmc:PMC13413154::1`：《A Lightweight Vision Transformer and Retinal Biomarker Fusion Framework for Early Glaucoma Detection: Toward Improved Clinical Screening.》 Abstract Background/Objectives: Glaucoma is a leading cause of irreversible blindness, and it is hard to catch early be-cause it rarely causes symptoms until real damage has already oc- curred. Existing au-t
- **datasets**：{"name": "ORIGA", "source": "Public domain fundus image dataset [33]", "split": "520 train (416 train, 104 val), 130 test"}；`valid` / `unverified`。
  - 3.1. Dataset Description / 页 5 / `europepmc:PMC13413154::13`：ing.》 3.1. Dataset Description Images of retinal fundus were obtained from the ORIGA and REFUGE2 datasets for the purpose of testing and training the suggested glaucoma screening method. The ORIGA
  - 3.1.1. ORIGA Dataset / 页 7 / `europepmc:PMC13413154::16`：ework for Early Glaucoma Detection: Toward Improved Clinical Screening.》 3.1.1. ORIGA Dataset J. Clin. Med. 2026, 15, 5651 7 of 26
- **metrics**：{"name": "Dice scores", "value": "0.9082 (OD), 0.9994 (OC), 0.9538 (mean)", "baseline": "", "task": null, "dataset": null, "split": null, "unit": null}；`valid` / `unverified`。
  - Abstract / 页 1 / `europepmc:PMC13413154::1`：a SoftMax classifier. Results: Segmentation accuracy was strong: Dice scores of 0.9082 forAcademic Editors: Su-Ho Lim and Daniel Laroche the optic disc and 0.9994 for the optic cup, IoU scores of 0.8351 and 0.9988, and an overall mean Dice of 0.9538 and mean IoU of 0.9169. Classification performance held up well, too,Received: 18 June 2026 Revised: 13 July 2026 with high F1-score, recall, accur
- **metrics**：{"name": "IoU scores", "value": "0.8351 (OD), 0.9988 (OC), 0.9169 (mean)", "baseline": "", "task": null, "dataset": null, "split": null, "unit": null}；`valid` / `unverified`。
  - Abstract / 页 1 / `europepmc:PMC13413154::1`：nd Daniel Laroche the optic disc and 0.9994 for the optic cup, IoU scores of 0.8351 and 0.9988, and an overall mean Dice of 0.9538 and mean IoU of 0.9169. Classification performance held up well, too,Received: 18 June 2026 Revised: 13 July 2026 with high F1-score, recall, accuracy, and precision acro

预算省略：`{'research_goal': 0, 'datasets': 1, 'metrics': 1}`；未匹配复核：0。

任务、划分、指标口径和单位未核对，不能直接排名。

## DrugPred: an EdgeConv-GNN and Bio_ClinicalBERT based polypharmacy ADR prediction and specialist recommendation model.

论文：`europepmc:PMC13462213`；索引版本：`3fa63da99a446a23f42afc25eb727cfdb0b850283ff91dadb4aca31fd4d8f05b`。

- **research_goal**："To predict ADR risks in polypharmacy settings and provide specialist recommendations by integrating individual drug effects, interaction statistics, and structural relationships."；`valid` / `unverified`。
  - 开篇 / 页 1 / `europepmc:PMC13462213::1`：lBERT based polypharmacy ADR prediction and deep learning-based DrugPred framework is proposed to predict ADR risks specialist recommendation model. by integrating individual drug effects, interaction statistics of the drugs, and Front. Artif. Intell. 9:1874825. structural relationships between drug-interaction graphs. In the ﬁrst phase,doi: 10. 3389/frai. 2026. 1874825
  - 开篇 / 页 1 / `europepmc:PMC13462213::2`：results indicate that our proposed DrugPred framework is effective in the prediction of ADR risks with high precision and recall. Overall, the proposed framework provides a scalable approach for predicting ADRs in multi-drug settings. We further extend our framework with a
- **datasets**：{"name": "OFFSIDES", "source": "Tatonetti et al. (2012)", "split": ""}；`valid` / `unverified`。
  - 12. Formulate a multimodal combined feature vect / 页 8 / `europepmc:PMC13462213::29`：ormulate a multimodal combined feature vect To maximize training eﬃciency, the OFFSIDES dataset was Xcombined = deduplicated to retain only unique drug-ADR pairs for downstream
  - 开篇 / 页 4 / `europepmc:PMC13462213::12`：ction and specialist recommendation model.》 开篇 (2012) extended their earlier OFFSIDES work attack detection and defense strategy in order to enhance the by releasing the TWOSIDES dataset, which focuses on single- strength of the proposed model
- **datasets**：{"name": "TwoSIDES", "source": "Tatonetti et al. (2012)", "split": ""}；`valid` / `unverified`。
  - 12. Formulate a multimodal combined feature vect / 页 11 / `europepmc:PMC13462213::35`：hen, aggregation, similar to the ﬁrst layer, is done, where for records from TWOSIDES, where each record corresponds to each drug i, Y(2)i is calculated as given in Equation 7. a unique (drug_A, drug_B, ADR_term) triplet. Each
  - 开篇 / 页 8 / `europepmc:PMC13462213::23`：10.3389/frai.2026.1874825 1. Input: OFFSIDES D1, TWOSIDES D2, patient drug set 13. Apply Attention Gate to prioritize relevant P = {d1, d2, . . . , dn}. features 2. Out
- **metrics**：{"name": "Accuracy", "value": "95.73%", "baseline": "", "task": null, "dataset": null, "split": null, "unit": null}；`missing` / `unverified`。
- **metrics**：{"name": "F1-score", "value": "0.94", "baseline": "", "task": null, "dataset": null, "split": null, "unit": null}；`missing` / `unverified`。
- **metrics**：{"name": "ROC-AUC", "value": "0.993", "baseline": "", "task": null, "dataset": null, "split": null, "unit": null}；`missing` / `unverified`。

预算省略：`{'research_goal': 0, 'datasets': 1, 'metrics': 1}`；未匹配复核：0。

任务、划分、指标口径和单位未核对，不能直接排名。

## Self-supervised Graph Neural Networks for Multi-behavior Recommendation

论文：`openalex:W4285600332`；索引版本：`dc2694a64aaca362e7569573da830057bbb74f419013ff9bbafceaea7d038394`。

- **research_goal**："提出一种新型的自监督图协同过滤模型S-MBRec，用于多行为推荐，旨在同时捕捉不同行为间的差异性和共性，并缓解监督信号稀疏问题。"；`valid` / `unverified`。
  - 3.2 Overall Framework • We propose a novel multi / 页 2 / `openalex:W4285600332::8`：supervised Graph Neural Networks for Multi-behavior Recommendation》 3.2 Overall Framework • We propose a novel multi model named S-MBRec, consisting of supervised and Figure 2 shows the overall framework of our S-MBRec self-supervised learning tasks. In particular, we design a model. As we can see, we first split the subgraphs of the 2053
  - 4.2 Overall Performance / 页 5 / `openalex:W4285600332::19`：raph Neural Networks for Multi-behavior Recommendation》 4.2 Overall Performance Evaluation Metrics. In order to fully evaluate the effec- We conduct a large number of experiments and recorded thetiveness of our model, we adopt two representative evalua- experimental results, which are shown in Table 2. In ordertion m
- **datasets**：{"name": "Beibei", "source": "https://www.beibei.com/", "split": "随机选择两条目标行为关联作为验证和测试数据，其余用于训练"}；`valid` / `unverified`。
  - 3.5 Star-style Self-supervised Task / 页 5 / `openalex:W4285600332::17`：4 0.0754 0.0633 0.1211 0.1366 0.1271 0.1263 0.1498 0.1647 Beibei Recall@80 0.0641 0.0933 0.0812 0.1939 0.1992 0.1923 0.1912 0.2017 0.2740 NDCG@10 0.0117 0.0121 0.0109 0.0127 0.0129
  - 3.5 Star-style Self-supervised Task / 页 5 / `openalex:W4285600332::18`：havior Recommendation》 3.5 Star-style Self-supervised Task (Take haviors.(Take Beibei and Yelp datasets as examples, and the evalu- Beibei and Yelp datasets as examples, and the evaluation index is ation index is Recall@80.)
- **datasets**：{"name": "Taobao", "source": "https://tianchi.aliyun.com/dataset/dataDetail?dataId=649", "split": "随机选择两条目标行为关联作为验证和测试数据，其余用于训练"}；`valid` / `unverified`。
  - 3.4 Adaptive Supervised Task / 页 4 / `openalex:W4285600332::13`：, Cart, Purchase} O+, which represents all unobserved interactions. Taobao 48749 39493 2.0×106 {Click, Cart, Purchase} Yelp 19800 22734 1.4×106 {Tip, Dislike, Neutral, Lik
  - 3.5 Star-style Self-supervised Task / 页 5 / `openalex:W4285600332::17`：04 0.0297 0.0224 0.0405 0.0487 0.0599 0.0706 0.0873 0.1027 Taobao Recall@80 0.0311 0.0763 0.0459 0.0795 0.0983 0.1030 0.1527 0.1559 0.1647 NDCG@10 0.0094 0.0105 0.0129 0.0216 0.0237 0
- **metrics**：{"name": "Recall@10", "value": "0.0529/0.0608/0.0259", "baseline": "MB-GMN:0.0497/0.0438/0.0243", "task": null, "dataset": null, "split": null, "unit": null}；`missing` / `unverified`。
- **metrics**：{"name": "NDCG@80", "value": "0.0615/0.0583/0.0438", "baseline": "MB-GMN:0.0465/0.0476/0.0416", "task": null, "dataset": null, "split": null, "unit": null}；`missing` / `unverified`。

预算省略：`{'research_goal': 0, 'datasets': 1, 'metrics': 0}`；未匹配复核：0。

任务、划分、指标口径和单位未核对，不能直接排名。
