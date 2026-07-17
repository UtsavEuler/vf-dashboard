# Ubiquitous Language

This glossary describes meanings implied by current code. Terms marked **confirm** need validation from Vehicle Finance team.

## Core business

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Vehicle Finance (VF)** | Euler function that connects Dealers and Customers with Financiers able to fund Euler vehicle purchases. | Finance dashboard |
| **Financier** | Bank, NBFC, leasing company, or fintech that can fund a vehicle purchase. | FI record, lender row |
| **Dealer** | Location selling Euler vehicles and coordinating Customer finance cases. | Dealer name alone |
| **Customer** | Person or company seeking finance for one or more vehicles. | Applicant, buyer |
| **Product** | Vehicle family being financed: 3WC, 3WP, 4WCS, or 4WCT. | Vehicle type |
| **Dealer–Financier Onboarding** | Relationship showing that one Financier can operate for one Dealer location and selected Products. | Mapping, link |
| **FI Policy** | Financier rules and commercial terms for one Product and Customer segment. | Eligibility data, criteria row |
| **Eligibility Result** | Matcher outcome: eligible, partial, ineligible, not applicable, or unknown. | Approval |
| **Geographic Override** | State-and-city-specific replacement for part of a base FI Policy. | Geo policy |

## Actors and organization

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **FI** | Short form for Financier or Financial Institution. | Finance |
| **POC** | Point of Contact responsible for follow-up. | Owner |
| **SPOC** | Single Point of Contact for a Financier or workflow. | POC when uniqueness matters |
| **VF POC** | Euler Vehicle Finance contact responsible for a Dealer. | Dealer owner |
| **RM** | Relationship Manager; exact employer and scope depend on table context. | POC |
| **ASM** | Area Sales Manager. | RM |
| **ZM** | Zonal Manager. | RM |

## Dealer and Product terms

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **DODO** | Dealer Owned, Dealer Operated location. | Independent dealer |
| **COCO** | Company Owned, Company Operated location. | Euler dealer |
| **3WC** | Three-wheeler cargo Product. | 3W |
| **3WP** | Three-wheeler passenger Product. | 3W Paxx, passenger vehicle |
| **4WCS** | Four-wheeler cargo Storm Product, inferred from code key `4wc_storm`. | 4WC |
| **4WCT** | Four-wheeler cargo Turbo Product, inferred from code key `4wc_turbo`. | 4WC |
| **Dealer Health** | Manually assigned Product-level status: star, green, amber, red, or N/A. | Performance, eligibility |

## Customer and credit terms

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Individual Customer** | Person applying for vehicle finance in personal capacity. | Individual segment |
| **Company-Owned Customer** | Business applying to own and finance vehicles. | CO customer, company segment |
| **CIBIL Score** | Indian credit score used by Financiers to assess repayment history. | Credit status |
| **NTC** | New to Credit: Customer has little or no established credit history. | No credit |
| **Existing CV** | Commercial vehicles already owned by Customer. | Existing vehicle |
| **Existing EV CV** | Existing commercial vehicles that are electric. | EV count |
| **ITR** | Income Tax Return or company financial evidence used to assess a business. | Tax document |
| **Business Vintage** | Number of years Company-owned Customer has operated. | Age |
| **Fleet Size** | Count of commercial vehicles owned or operated by Company-owned Customer. | Vehicle count |
| **Turnover Tier** | Allowed annual-turnover range, optionally paired with minimum Fleet Size. | Loan tier |
| **FTU** | Customer classification used by FI Policy; expansion is not defined in code. **Confirm.** | First-time user until confirmed |
| **FTB** | Customer classification used by FI Policy; expansion is not defined in code. **Confirm.** | First-time buyer/borrower until confirmed |

## Financing terms

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Loan Tenure** | Maximum repayment period, stored in months. | Duration |
| **ESP** | Ex-showroom price used as loan calculation base. | Vehicle cost |
| **ORP** | On-road price used as loan calculation base. | Vehicle cost |
| **Max Loan Amount** | Financier cap expressed as rupees, percentage of vehicle cost, or both. | Funding |
| **IRR** | Internal Rate of Return used here as a comparable financing-rate field; business interpretation needs confirmation. | Interest rate |
| **Guarantor** | Additional person or entity responsible if Customer does not repay. | Co-applicant |
| **DL** | Driving Licence. | Licence |
| **LLR** | Learner's Licence. | DL |
| **OL** | Finance instrument code listed in FI Master. Expansion is not defined. **Confirm.** | Operating lease until confirmed |
| **TL** | Finance instrument code listed in FI Master. Expansion is not defined. **Confirm.** | Term loan until confirmed |

## Financier lifecycle

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **MoU** | Memorandum of Understanding between Euler and Financier. | Contract |
| **WIP** | Work in Progress. | Pending |
| **Financier Status** | Relationship stage such as Active, Onboarded, Discussion, On Hold, Suspended, or Not Started. | Dealer onboarding status |
| **Priority** | P1 or P2 business ranking assigned to a Financier. | Eligibility rank |
| **TTL** | Field labelled “Time to Live” in UI, but business meaning is unclear. **Confirm.** | Turnaround time until confirmed |

## TA, IF, and retail-code workflow

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **TA** | Dealer funding workflow tracked separately from Customer vehicle loans; expansion is not defined. **Confirm.** | Trade Advance until confirmed |
| **IF** | Dealer funding workflow tracked separately from Customer vehicle loans; expansion is not defined. **Confirm.** | Inventory Funding until confirmed |
| **Retail Code** | Financier-side Dealer identifier needed before Dealer can submit or receive financed retail business. | Dealer code |
| **Code Opening** | Process of collecting Dealer documents and creating Retail Code with a Financier. | Onboarding |
| **SL** | Sanction Letter showing approved facility or amount. | Approval |
| **Disbursement** | Financier releases approved funds. | Approval, sanction |

## Relationships

- One **Dealer** is identified by name plus location; name alone is not unique.
- One **Dealer** can have many **Dealer–Financier Onboardings**.
- One **Financier** can be onboarded with many Dealers and Products.
- One **FI Policy** belongs to one Financier and one Product.
- One FI Policy contains separate Individual and Company-owned Customer rules.
- Geographic Override modifies one segment of one FI Policy for one state and city.
- Eligibility Result recommends possible Financiers; it is not loan approval.
- Retail Code belongs to one Dealer–Financier relationship.

## Main workflow

1. Maintain Dealer and Financier master lists.
2. Record which Financiers are active with each Dealer and Product.
3. Enter FI Policy rules for each Financier and Product.
4. Optionally override rules for a state and city.
5. Enter Customer profile in eligibility matcher.
6. Matcher filters to Financiers already onboarded for selected Dealer and Product.
7. Matcher compares Customer profile against FI Policy and ranks possible Financiers.
8. Human team contacts Financier and continues real application, sanction, and disbursement outside this matcher.

## Example dialogue

> **Developer:** “Why does Customer not see every Financier?”
>
> **VF expert:** “Matcher first checks Dealer–Financier Onboarding for selected Dealer location and Product. Then it checks FI Policy.”
>
> **Developer:** “So eligible means loan approved?”
>
> **VF expert:** “No. Eligibility Result means policy appears compatible. Financier still reviews documents, issues SL, and later makes Disbursement.”
>
> **Developer:** “Is Retail Code same as Customer loan?”
>
> **VF expert:** “No. Retail Code enables Dealer to work with Financier. Customer application comes afterward.”

## Flagged ambiguities

- `onboarding` means both Euler–Financier relationship stage and Dealer–Financier enablement. Use **Financier Status** for first, **Dealer–Financier Onboarding** for second.
- `CO` means Company-owned Customer in FI Policy, while `COCO` means Company Owned, Company Operated Dealer. Never shorten Company-owned Customer to CO in user-facing text.
- Dealer identity sometimes uses name only and sometimes name plus location. Canonical identity must be name plus location until stable Dealer ID exists.
- **FTU**, **FTB**, **TA**, **IF**, **OL**, **TL**, and **TTL** need domain-owner confirmation.
- **IRR** may be presented like customer interest rate, but finance teams may use it as lender return/yield. Confirm before changing ranking or labels.
- Dealer Health colors lack documented business definitions; confirm what star/green/amber/red measure.
