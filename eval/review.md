# Answers to grade by hand

For each: is the answer correct and supported by its sources? Write correct / partly / wrong / refused next to Verdict, optionally followed by a comment. Verdicts are saved to hand_grades.json on the next run of run_eval.py; one already filled in was given to the same answer before. Leave the <!-- answer:... --> lines as they are.

## 1. [narrative_comprehension] What risks does Apple disclose related to tariffs and international trade restrictions?

**Expected:** Apple discloses that new U.S. tariffs on imports (including from China, India, Japan, South Korea, Taiwan, Vietnam, and the EU) beginning in Q2 FY2025, along with retaliatory tariffs from other countries, could increase costs, limit availability of components, force supplier/business restructuring, and disrupt its heavily outsourced, geographically concentrated supply chain.

**System (answered, band=medium, confidence=0.588):** Apple warns that tariffs and other international trade restrictions could materially adversely affect its business and supply chain, especially in regions where it generates significant revenue or has key manufacturing operations [1]. These measures may increase the cost or limit the availability of its products, components, rare earths and other raw materials, potentially requiring supplier changes, restructuring of relationships, or cessation of product offerings, and could lead to higher product prices [4][5]. The company also notes that new or increased tariffs can create volatility and downward pressure on net sales, gross margins, pricing and overall financial condition, with uncertainty about the magnitude, duration, and possible retaliatory actions [2][6]. Additionally, abrupt trade restrictions can generate uncertainty, disrupt supply chains, and negatively impact consumer spending and sentiment, further harming results of operations [3].

- [1] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [4] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [5] AAPL | 10-K | FY2025 | Item 1 | Business
- [2] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [6] AAPL | 10-K | FY2025 | Item 7 | Management’s Discussion and Analysis of Financial Condition and Results of Operations
- [3] AAPL | 10-K | FY2025 | Item 1A | Risk Factors

<!-- answer:6d46554400ef -->
**Verdict:** 

## 2. [narrative_comprehension] What cybersecurity-related risks does Apple describe in its filing?

**Expected:** Apple states it regularly experiences malicious attacks and unauthorized access attempts targeting confidential and personal information; it is considered a higher-value target due to its high profile; and despite security measures like encryption and multifactor authentication, it cannot guarantee absolute protection, which could lead to reputational, financial, legal, and operational harm.

**System (answered, band=medium, confidence=0.496):** Apple describes cybersecurity-related risks such as exposure to information technology system failures or network disruptions caused by computer viruses, ransomware, or other cybersecurity incidents [2]. It notes that system upgrades, redundancy, continuity measures, and business continuity and disaster recovery planning may be ineffective or inadequate [2]. The company is vulnerable to hacking, ransomware attacks, employee error, malfeasance, system error, faulty password management, and other irregularities [3]. Third parties can fraudulently induce employees or customers to disclose usernames, passwords, or other sensitive information, leading to unauthorized access [3]. Apple experiences malicious attacks that target the confidentiality, integrity, or availability of information and can disrupt operations, damage reputation, affect its stock price, and expose it to litigation or fines [4]. It expects attacks to accelerate in frequency and sophistication, using tools that circumvent controls, avoid detection, and remove forensic evidence, hindering investigation and recovery [4]. Errors, bugs, and vulnerabilities in hardware, software, and services, including those introduced by new technologies such as artificial intelligence, can be exploited by third parties, compromising device safety and security [5]. Apple’s enterprise risk management program is designed to identify, assess, and monitor these cybersecurity risks [1].

- [2] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [3] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [4] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [5] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [1] AAPL | 10-K | FY2025 | Item 1C | Cybersecurity

<!-- answer:8a096937122a -->
**Verdict:** 

## 3. [narrative_comprehension] What risk does Apple disclose regarding natural disasters and its facilities?

**Expected:** Many of Apple's operations, retail stores, and supplier/contract manufacturer facilities are located in areas prone to earthquakes and other natural disasters, and climate change is increasing the frequency and intensity of extreme weather, which could disrupt operations.

**System (answered, band=high, confidence=0.835):** Apple discloses that many of its operations, retail stores and facilities – as well as those of its suppliers and contract manufacturers – are located in areas prone to earthquakes and other natural disasters, creating a risk of interruption to its business operations [1].

- [1] AAPL | 10-K | FY2025 | Item 1A | Risk Factors

<!-- answer:baeac02c8a54 -->
**Verdict:** 

## 4. [narrative_comprehension] Why did Wearables, Home and Accessories net sales decrease in FY2025?

**Expected:** Primarily due to lower net sales of Accessories and Wearables.

**System (answered, band=high, confidence=1.0):** Wearables, Home and Accessories net sales fell 4% to $35,686 million in 2025 from $37,005 million in 2024 [2]. The decline was primarily due to lower net sales of Accessories and Wearables [1].

- [2] AAPL | 10-K | FY2025 | Item 7 | Management’s Discussion and Analysis of Financial Condition and Results of Operations
- [1] AAPL | 10-K | FY2025 | Item 7 | Management’s Discussion and Analysis of Financial Condition and Results of Operations

<!-- answer:5d11fe30f805 -->
**Verdict:** 

## 5. [narrative_comprehension] Why did Services net sales increase in FY2025?

**Expected:** Primarily due to higher net sales from advertising, the App Store, and cloud services.

**System (answered, band=high, confidence=1.0):** Services net sales increased because higher net sales from advertising, the App Store, and cloud services drove the growth [1].

- [1] AAPL | 10-K | FY2025 | Item 7 | Management’s Discussion and Analysis of Financial Condition and Results of Operations

<!-- answer:2745fd4c5e7c -->
**Verdict:** 

## 6. [narrative_comprehension] What risk does Apple disclose about acquisitions and new business investments?

**Expected:** Investments in new business strategies, commercial relationships, and acquisitions carry risks including management distraction, unexpected liabilities, regulatory challenges, inadequate returns, potential asset impairment, and failure to obtain regulatory approval or realize anticipated benefits.

**System (answered, band=high, confidence=0.91):** Apple warns that acquisitions and new business investments may face regulatory approval delays or failures, counterparty performance issues, onerous conditions, management distraction, unexpected liabilities and expenses, economic, political, legal and regulatory challenges, inadequate returns, asset impairments, write‑offs, and could disrupt its business and materially adversely affect its reputation, results of operations, financial condition and stock price [1][2][3].

- [1] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [2] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [3] AAPL | 10-K | FY2025 | Item 1A | Risk Factors

<!-- answer:d1990bd48114 -->
**Verdict:** 

## 7. [narrative_comprehension] What does Apple disclose about legal proceedings and government investigations as a risk?

**Expected:** Apple is subject to various claims, legal proceedings, and government investigations arising in the ordinary course of business, some unresolved, with new matters possibly arising in the future; the number and scale of such matters has generally increased over time.

**System (answered, band=medium, confidence=0.688):** Apple discloses that legal proceedings and government investigations could materially adversely affect its business, results of operations, financial condition, and stock price, and that the number and magnitude of such matters have generally increased and may continue to increase [1]. The outcomes of these matters are uncertain and could result in significant monetary damages, disgorgement, remedial corporate measures, injunctive relief, and may require changes to business practices [4]. Apple is subject to antitrust investigations and litigation in multiple jurisdictions, including civil lawsuits in the U.S. alleging monopolization and investigations in Europe related to App Store terms, which could have a material adverse impact on the company [6]. The company also faces intense regulatory scrutiny and potential penalties for privacy and data‑protection compliance failures, which could lead to inquiries, proceedings, and reputational impacts [3]. New legal matters may arise, prompting further business changes in response to litigation and regulatory requirements [2][5].

- [1] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [4] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [6] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [3] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [2] AAPL | 10-K | FY2025 | Item 1A | Risk Factors
- [5] AAPL | 10-K | FY2025 | Item 1A | Risk Factors

<!-- answer:1b84f53cc805 -->
**Verdict:** 

## 8. [cross_referencing] Does Apple's FY2025 balance sheet satisfy Assets = Liabilities + Equity?

**Expected:** Yes. $285,508,000,000 (liabilities) + $73,733,000,000 (equity) = $359,241,000,000, which exactly matches total assets.

**System (answered, band=medium, confidence=0.562):** Yes. Total assets are $359,241 million [S1]; total liabilities are $285,508 million [S2] and total shareholders’ equity is $73,733 million [S3]; liabilities plus equity equal $359,241 million, matching assets.

- [S1] AAPL | 10-K | FY2025 | balance sheet - Total assets
- [S2] AAPL | 10-K | FY2025 | balance sheet - Total liabilities
- [S3] AAPL | 10-K | FY2025 | balance sheet - Total shareholders’ equity
- *note: answer rests on exact values from the financial statements*
- *note: weakest cited figure was graded 0.50*

<!-- answer:03841460160b -->
**Verdict:** 

## 9. [cross_referencing] Do Apple's FY2025 cash flow activities (operating, investing, financing) reconcile with the reported net change in cash?

**Expected:** Yes. $111,482,000,000 (operating) + $15,195,000,000 (investing) - $120,686,000,000 (financing) = $5,991,000,000, which exactly matches the reported 'Increase in cash' line.

**System (answered, band=medium, confidence=0.657):** Operating cash flow was $111,482 million [1]. Investing cash flow was $15,195 million [3]. Financing cash flow was $(120,686) million [2]. The sum of these three amounts is $5,991 million, which matches the reported increase in cash of $5,991 million [2].

- [1] AAPL | 10-K | FY2025 | Item 8 | Financial Statements and Supplementary Data
- [3] AAPL | 10-K | FY2025 | Item 8 | Financial Statements and Supplementary Data
- [2] AAPL | 10-K | FY2025 | Item 8 | Financial Statements and Supplementary Data

<!-- answer:11d000b47d78 -->
**Verdict:** 

## 10. [cross_referencing] Apple's diluted EPS grew faster (22.7%) than its net income (19.5%) from FY2024 to FY2025. What explains the gap?

**Expected:** Diluted shares outstanding fell from ~15.41 billion (FY2024) to ~15.00 billion (FY2025), a ~2.6% reduction, driven by Apple's ongoing share repurchase program ($90,711,000,000 spent on stock buybacks in FY2025). Fewer shares outstanding means the same or higher net income is divided across a smaller share count, boosting EPS growth beyond net income growth alone.

**System (answered, band=medium, confidence=0.551):** The faster EPS growth reflects a decline in diluted share count. Diluted shares fell from 15,408,095 thousand in FY2024 to 15,004,697 thousand in FY2025, a drop of about 2.6% [1]. At the same time the effect of dilutive share‑based awards fell from 64,312 thousand to 56,197 thousand shares, further reducing the denominator [1]. With net income rising 19.5% (from $93,736 million to $112,010 million) and a smaller share base, diluted EPS rose 22.7% (from $6.08 to $7.46) [1].

- [1] AAPL | 10-K | FY2025 | Item 8 | Financial Statements and Supplementary Data

<!-- answer:53ef7091d307 -->
**Verdict:** 

## 11. [cross_referencing] Apple's Products gross margin dollars increased in FY2025 ($112,887M vs $109,633M in FY2024) but Products gross margin percentage decreased (36.8% vs 37.2%). What explains this?

**Expected:** Per Apple's MD&A: margin dollars rose primarily due to favorable costs and a different mix of products, partially offset by tariff costs; the margin percentage fell primarily due to a different mix of products and tariff costs, partially offset by other favorable costs. The filing does not cite sales volume as a reason.

**System (answered, band=high, confidence=0.993):** The Products gross margin rose to $112,887 million in FY2025 from $109,633 million in FY2024 [2], driven by favorable costs and a different product mix, partially offset by tariff costs [1]. However, the margin percentage fell to 36.8% from 37.2% [2] because the mix shift and tariff costs reduced the overall margin rate, with only partial offset from other favorable costs [1].

- [2] AAPL | 10-K | FY2025 | Item 7 | Management’s Discussion and Analysis of Financial Condition and Results of Operations
- [1] AAPL | 10-K | FY2025 | Item 7 | Management’s Discussion and Analysis of Financial Condition and Results of Operations

<!-- answer:17e7f8989040 -->
**Verdict:** 

## 12. [cross_referencing] Apple's net income dipped from $96,995M in FY2023 to $93,736M in FY2024 before rising sharply to $112,010M in FY2025. What caused the FY2024 dip?

**Expected:** A one-time income tax charge of $10.2 billion, net, recorded in Q4 FY2024 related to the European Commission's State Aid Decision (a 2016 ruling on Irish tax treatment of Apple subsidiaries, upheld by the European Court of Justice in September 2024). This depressed FY2024 net income; the charge did not recur in FY2025, contributing to the sharp rebound.

**System (answered, band=high, confidence=0.754):** Apple’s FY2024 net‑income decline was driven by a much higher provision for income taxes [3]. The tax provision jumped to $29,749 million in FY2024 from $16,741 million in FY2023 [2]. This raised the effective tax rate to 24.1% from 14.7% [3]. Net income fell to $93,736 million from $96,995 million [1]. The increase was largely due to a surge in foreign current taxes and a $10,246 million impact of the State Aid Decision [3].

- [3] AAPL | 10-K | FY2025 | Item 8 | Financial Statements and Supplementary Data
- [2] AAPL | 10-K | FY2025 | Item 8 | Financial Statements and Supplementary Data
- [1] AAPL | 10-K | FY2025 | Item 8 | Financial Statements and Supplementary Data
- *note: needed 2 retrieval attempts*
- *note: found on a second search: the first evidence didn't answer the question, so it was split into parts (Operating income for FY2024 and FY2023 as reported in Apple’s Form 10‑K; Other income and other expenses for FY2024 and FY2023 as reported in Apple’s Form 10‑K; Provision for income taxes for FY2024 and FY2023 as reported in Apple’s Form 10‑K; One‑time items (e.g., impairment charges, restructuring costs, legal settlements) reported in FY2024 in Apple’s Form 10‑K)*

<!-- answer:ccea2fe2da50 -->
**Verdict:** 

## 13. [trend] Did Apple's Services revenue grow faster than its iPhone revenue between FY2024 and FY2025?

**Expected:** Yes. Services grew ~13.5% (from $96,169M to $109,158M) while iPhone grew ~4.2% (from $201,183M to $209,586M).

**System (answered, band=high, confidence=0.9):** Services revenue was $96,169 million in FY2024 [S1] and $109,158 million in FY2025 [S2]; iPhone revenue was $201,183 million in FY2024 [S3] and $209,586 million in FY2025 [S4]. Services grew by $12,989 million (≈13.5%) versus iPhone growth of $8,403 million (≈4.2%) [S1][S2][S3][S4]; thus Services revenue grew faster [S1][S2][S3][S4].

- [S1] AAPL | 10-K | FY2024 | income statement - Services
- [S2] AAPL | 10-K | FY2025 | income statement - Services
- [S3] AAPL | 10-K | FY2024 | income statement - iPhone
- [S4] AAPL | 10-K | FY2025 | income statement - iPhone
- *note: answer rests on exact values from the financial statements*

<!-- answer:f6cda0aa1c94 -->
**Verdict:** 

## 14. [trend] What was the 3-year trend (FY2023-FY2025) in Apple's net income?

**Expected:** $96,995M (FY2023) -> $93,736M (FY2024, a ~3.4% dip) -> $112,010M (FY2025, a ~19.5% rise). Non-monotonic: a decline followed by a sharp rebound.

**System (answered, band=high, confidence=0.9):** Apple's net income was $96,995 million in FY2023 [S1], fell to $93,736 million in FY2024 [S2], then rose to $112,010 million in FY2025 [S3], showing a decline in 2024 [S2] followed by a strong increase in 2025 [S3].

- [S1] AAPL | 10-K | FY2023 | income statement - Net income
- [S2] AAPL | 10-K | FY2024 | income statement - Net income
- [S3] AAPL | 10-K | FY2025 | income statement - Net income
- *note: answer rests on exact values from the financial statements*

<!-- answer:f77b1c4d1227 -->
**Verdict:** 

## 15. [trend] What was the 3-year trend (FY2023-FY2025) in Apple's diluted EPS?

**Expected:** $6.13 (FY2023) -> $6.08 (FY2024, slight dip) -> $7.46 (FY2025, sharp rise). Follows the same pattern as net income, amplified by a shrinking share count from buybacks.

**System (answered, band=high, confidence=0.9):** Apple’s diluted earnings per share were $6.13 per share in FY2023 [S1], $6.08 per share in FY2024 [S2], and $7.46 per share in FY2025 [S3], indicating a slight decline from FY2023 to FY2024 [S2] and a notable increase in FY2025 [S3].

- [S1] AAPL | 10-K | FY2023 | income statement - Diluted (in dollars per share)
- [S2] AAPL | 10-K | FY2024 | income statement - Diluted (in dollars per share)
- [S3] AAPL | 10-K | FY2025 | income statement - Diluted (in dollars per share)
- *note: answer rests on exact values from the financial statements*

<!-- answer:23e977bd7881 -->
**Verdict:** 

## 16. [trend] How did Apple's total liabilities change from FY2024 to FY2025?

**Expected:** Decreased from $308,030M to $285,508M, a reduction of about $22,522M (~7.3%).

**System (answered, band=high, confidence=0.9):** Apple's total liabilities decreased by $22,522 million [S1][S2], from $308,030 million in FY2024 [S1] to $285,508 million in FY2025 [S2].

- [S1] AAPL | 10-K | FY2024 | balance sheet - Total liabilities
- [S2] AAPL | 10-K | FY2025 | balance sheet - Total liabilities
- *note: answer rests on exact values from the financial statements*

<!-- answer:4d4dad33fb1f -->
**Verdict:** 
