# Hand check: Colorado CAP citation set (test split)

Sample: 50 direct + 50 indirect test queries, `random.Random(41)` shuffle (`handcheck_sample.py`),
full text in `handcheck-sample.txt`. Each item was read by hand: is the gold the case cited at the
masked spot, and does the masked text still give the gold case away?

| Check | Result |
| --- | --- |
| Gold is the case cited at the masked spot (gold reporter cite appears in the raw paragraph) | 100/100 |
| Wrong gold | 0/100 |
| Identity leak (gold's cite or distinctive party name left in the query) | 0/100 |
| Minor defects | 5/100: 3 keep the subsequent-history tail after the mask ("[CITATION], aff'd, 198 P.3d 589"; "rev'd on other grounds, 795 P.2d 266"; "as modified on denial of reh'g"), 1 over-mask (the mask also took the previous sentence's caption-like words), 1 "In re [CITATION]" leftover |
| **Error rate** | **0% hard errors, 5% minor defects** |
| Valid but ambiguous: boilerplate propositions (standard of review, de novo statutory interpretation, abuse of discretion) that many cases support | about 16/100 |

The first version of the masker was checked on the same 100-item draw before these fixes:
- 10/100 items had a defect: a partial caption before the mask ("Colo. Div. of Emp't & [CITATION]", "Mountainwood Condo. [CITATION]", "Wash. [CITATION]");
- an unmasked public-domain parallel cite ("2016 COA 103", "2013 COA 110M");
- a same-caption subsequent history ("Hewitt v. Rice, 119 P.3d 541, aff'd, [CITATION]").

What was fixed:
- name walk-back over abbreviated captions;
- masking of glued parallel cites;
- dropping queries where the target is the subsequent history of a case named in the paragraph;
- trimming leftover parallel-cite residue.

The set was then rebuilt and re-sealed.

The ambiguous boilerplate items are kept because CLERC and LePaRD keep them too. Gold is the cited case, not every case that would support the proposition, so absolute recall understates usefulness.
